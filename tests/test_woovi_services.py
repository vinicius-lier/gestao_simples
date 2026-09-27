from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from financeiro.models import CobrancaPix, ContaRecebimento, Mensalidade, Repasse
from integracoes.woovi.chave_pix import ChavePixInvalida, normalizar_chave_pix
from integracoes.woovi.client import Cobranca
from integracoes.woovi.exceptions import WooviInvalidResponseError, WooviRequestError, WooviUnavailableError
from integracoes.woovi.services import (
    RecebimentoBloqueado,
    RecebimentoNaoConfigurado,
    conferir_pagamento_pix,
    configurar_chave_pix,
    garantir_cobranca_pix,
    registrar_pagamento_pix,
    remover_cobranca_pix,
    solicitar_repasse,
)
from tests.woovi_base import CHAVE, CenarioWoovi, cobranca_criada, subconta


class ChavePixTests(SimpleTestCase):
    def test_normaliza_cada_tipo(self):
        casos = [
            (ContaRecebimento.CPF, "529.982.247-25", "52998224725"),
            (ContaRecebimento.CNPJ, "11.222.333/0001-81", "11222333000181"),
            (ContaRecebimento.EMAIL, " Escola@Keiko.com.BR ", "escola@keiko.com.br"),
            (ContaRecebimento.TELEFONE, "(21) 99999-8888", "+5521999998888"),
            (ContaRecebimento.TELEFONE, "+55 21 99999-8888", "+5521999998888"),
            (ContaRecebimento.ALEATORIA, "9134E286-6F71-427A-BF00-241681624587", "9134e286-6f71-427a-bf00-241681624587"),
        ]
        for tipo, entrada, esperado in casos:
            with self.subTest(tipo=tipo, entrada=entrada):
                self.assertEqual(normalizar_chave_pix(tipo, entrada), esperado)

    def test_chaves_invalidas(self):
        casos = [
            (ContaRecebimento.CPF, "529.982.247-26"),  # dígito errado
            (ContaRecebimento.CPF, "111.111.111-11"),
            (ContaRecebimento.CNPJ, "11.222.333/0001-80"),
            (ContaRecebimento.EMAIL, "não-é-email"),
            (ContaRecebimento.TELEFONE, "99999-8888"),
            (ContaRecebimento.ALEATORIA, "abc"),
            (ContaRecebimento.EMAIL, ""),
            ("desconhecido", "x"),
        ]
        for tipo, entrada in casos:
            with self.subTest(tipo=tipo, entrada=entrada), self.assertRaises(ChavePixInvalida):
                normalizar_chave_pix(tipo, entrada)


@patch("integracoes.woovi.services.WooviClient")
class ConfigurarChavePixTests(CenarioWoovi, TestCase):
    def setUp(self):
        self.criar_cenario()
        self.conta.delete()

    def test_cria_a_subconta_e_a_conta_de_recebimento(self, mock_client):
        mock_client.return_value.criar_ou_obter_subconta.return_value = subconta()

        conta = configurar_chave_pix(self.academia, ContaRecebimento.EMAIL, "Escola@Keiko.com.br", self.usuario)

        mock_client.return_value.criar_ou_obter_subconta.assert_called_once_with(CHAVE, "Escola de Judô Keiko Fukuda")
        self.assertEqual(conta.pix_key, CHAVE)
        self.assertTrue(conta.ativa)
        self.assertEqual(conta.criada_por, self.usuario)

    def test_mesma_chave_de_novo_nao_chama_o_provedor(self, mock_client):
        mock_client.return_value.criar_ou_obter_subconta.return_value = subconta()
        primeira = configurar_chave_pix(self.academia, ContaRecebimento.EMAIL, CHAVE)

        segunda = configurar_chave_pix(self.academia, ContaRecebimento.EMAIL, CHAVE.upper())

        self.assertEqual(primeira, segunda)
        mock_client.return_value.criar_ou_obter_subconta.assert_called_once()

    def test_subconta_ja_existente_no_provedor_e_reaproveitada(self, mock_client):
        mock_client.return_value.criar_ou_obter_subconta.return_value = subconta(saldo=5000)
        conta = configurar_chave_pix(self.academia, ContaRecebimento.EMAIL, CHAVE)
        self.assertEqual(ContaRecebimento.objects.get(), conta)

    def test_chave_invalida_nao_chama_o_provedor(self, mock_client):
        with self.assertRaises(ChavePixInvalida):
            configurar_chave_pix(self.academia, ContaRecebimento.CPF, "123.456.789-00")
        mock_client.assert_not_called()
        self.assertFalse(ContaRecebimento.objects.exists())

    def test_provedor_recusando_a_chave_nao_grava_nada(self, mock_client):
        mock_client.return_value.criar_ou_obter_subconta.side_effect = WooviRequestError("chave inválida", 400)
        with self.assertRaises(WooviRequestError):
            configurar_chave_pix(self.academia, ContaRecebimento.EMAIL, CHAVE)
        self.assertFalse(ContaRecebimento.objects.exists())

    def test_chave_com_saque_bloqueado_gera_alerta(self, mock_client):
        mock_client.return_value.criar_ou_obter_subconta.return_value = subconta(bloqueada=True)
        with self.assertLogs("gestao.alertas", level="ERROR"):
            conta = configurar_chave_pix(self.academia, ContaRecebimento.EMAIL, CHAVE)
        self.assertTrue(conta.saque_bloqueado)


@patch("integracoes.woovi.services.WooviClient")
class TrocarChavePixTests(CenarioWoovi, TestCase):
    NOVA = "financeiro@keiko.com.br"

    def setUp(self):
        self.criar_cenario()

    def trocar(self):
        return configurar_chave_pix(self.academia, ContaRecebimento.EMAIL, self.NOVA, self.usuario)

    def test_troca_desativa_a_antiga_e_ativa_a_nova(self, mock_client):
        mock_client.return_value.criar_ou_obter_subconta.return_value = subconta(chave=self.NOVA)

        nova = self.trocar()

        self.conta.refresh_from_db()
        self.assertFalse(self.conta.ativa)
        self.assertIsNotNone(self.conta.desativada_em)
        self.assertEqual(self.conta.desativada_por, self.usuario)
        self.assertEqual(ContaRecebimento.ativa_da(self.academia), nova)
        mock_client.return_value.criar_ou_obter_subconta.assert_called_once_with(self.NOVA, "Escola de Judô Keiko Fukuda")

    def test_bloqueada_com_repasse_em_andamento(self, mock_client):
        for status in Repasse.STATUS_ABERTOS:
            with self.subTest(status=status):
                repasse = Repasse.objects.create(
                    academia=self.academia, conta_recebimento=self.conta, pix_key_destino=CHAVE, status=status,
                )
                with self.assertRaises(RecebimentoBloqueado):
                    self.trocar()
                mock_client.assert_not_called()
                self.conta.refresh_from_db()
                self.assertTrue(self.conta.ativa)
                repasse.delete()

    def test_cancela_os_pix_nao_pagos_da_chave_antiga_e_preserva_o_historico(self, mock_client):
        mock_client.return_value.criar_ou_obter_subconta.return_value = subconta(chave=self.NOVA)
        vigente = self.cobranca(correlation_id="c-vigente")
        expirada = self.cobranca(mensalidade=self.nova_mensalidade(date(2026, 8, 1)), correlation_id="c-exp", dias=-1)
        paga = self.cobranca(
            mensalidade=self.nova_mensalidade(date(2026, 7, 1), status="paga"),
            correlation_id="c-paga", status=CobrancaPix.PAGA,
        )
        concluido = Repasse.objects.create(
            academia=self.academia, conta_recebimento=self.conta, pix_key_destino=CHAVE, status=Repasse.CONCLUIDA,
        )

        self.trocar()

        mock_client.return_value.remover_cobranca.assert_called_once_with("c-vigente")
        for cobranca, status in ((vigente, CobrancaPix.CANCELADA), (expirada, CobrancaPix.EXPIRADA), (paga, CobrancaPix.PAGA)):
            cobranca.refresh_from_db()
            self.assertEqual(cobranca.status, status)
            self.assertEqual(cobranca.conta_recebimento, self.conta)  # vínculo original preservado
        concluido.refresh_from_db()
        self.assertEqual(concluido.conta_recebimento, self.conta)
        self.assertEqual(ContaRecebimento.objects.filter(academia=self.academia).count(), 2)

    def test_falha_ao_cancelar_pix_antigo_nao_troca_nada(self, mock_client):
        mock_client.return_value.criar_ou_obter_subconta.return_value = subconta(chave=self.NOVA)
        mock_client.return_value.remover_cobranca.side_effect = WooviUnavailableError("fora")
        cobranca = self.cobranca()

        with self.assertRaises(WooviUnavailableError):
            self.trocar()

        self.conta.refresh_from_db()
        self.assertTrue(self.conta.ativa)
        self.assertEqual(ContaRecebimento.objects.count(), 1)
        cobranca.refresh_from_db()
        self.assertEqual(cobranca.status, CobrancaPix.ATIVA)

    def test_depois_da_troca_o_proximo_pix_usa_a_chave_nova(self, mock_client):
        mock_client.return_value.criar_ou_obter_subconta.return_value = subconta(chave=self.NOVA)
        mock_client.return_value.criar_cobranca.side_effect = cobranca_criada
        self.cobranca()
        self.trocar()

        cobranca = garantir_cobranca_pix(self.mensalidade)

        self.assertEqual(cobranca.conta_recebimento.pix_key, self.NOVA)


@patch("integracoes.woovi.services.WooviClient")
class GarantirCobrancaPixTests(CenarioWoovi, TestCase):
    def setUp(self):
        self.criar_cenario()

    def test_cria_pix_sem_split(self, mock_client):
        mock_client.return_value.criar_cobranca.side_effect = cobranca_criada

        cobranca = garantir_cobranca_pix(self.mensalidade)

        kwargs = mock_client.return_value.criar_cobranca.call_args.kwargs
        self.assertEqual(kwargs["valor_centavos"], 12000)
        # A Woovi recusa split de 100% (HTTP 400 em produção) e o valor é todo
        # da academia: o Pix vai sem split e o líquido é creditado depois.
        self.assertNotIn("splits", kwargs)
        self.assertTrue(kwargs["correlation_id"].startswith(f"mensalidade-{self.mensalidade.pk}-"))
        self.assertEqual(kwargs["comentario"], "Mensalidade 09/2026 - Ana")
        self.assertEqual(kwargs["cliente"], {"name": "Maria", "taxID": "52998224725", "phone": "5521999998888"})
        self.assertEqual(cobranca.conta_recebimento, self.conta)
        self.assertEqual(cobranca.br_code, "00020126PIX")
        self.assertTrue(self.mensalidade.pix_vigente)

    def test_idempotente_reaproveita_o_pix_vigente(self, mock_client):
        mock_client.return_value.criar_cobranca.side_effect = cobranca_criada
        primeira = garantir_cobranca_pix(self.mensalidade)
        segunda = garantir_cobranca_pix(Mensalidade.objects.get(pk=self.mensalidade.pk))
        self.assertEqual(primeira, segunda)
        mock_client.return_value.criar_cobranca.assert_called_once()

    def test_pix_perto_de_expirar_e_substituido(self, mock_client):
        mock_client.return_value.criar_cobranca.side_effect = cobranca_criada
        antiga = self.cobranca()
        antiga.expira_em = timezone.now() + timedelta(minutes=10)
        antiga.save()

        nova = garantir_cobranca_pix(self.mensalidade)

        self.assertNotEqual(nova, antiga)
        mock_client.return_value.remover_cobranca.assert_called_once_with(antiga.correlation_id)
        antiga.refresh_from_db()
        self.assertEqual(antiga.status, CobrancaPix.CANCELADA)

    def test_sem_chave_de_recebimento_nao_gera(self, mock_client):
        self.conta.ativa = False
        self.conta.save()
        with self.assertRaises(RecebimentoNaoConfigurado):
            garantir_cobranca_pix(self.mensalidade)
        mock_client.assert_not_called()

    def test_mensalidade_vencida_gera_e_paga_nao(self, mock_client):
        mock_client.return_value.criar_cobranca.side_effect = cobranca_criada
        self.mensalidade.status = "vencida"
        self.mensalidade.save()
        garantir_cobranca_pix(self.mensalidade)

        paga = self.nova_mensalidade(date(2026, 8, 1), status="paga")
        with self.assertRaisesMessage(ValueError, "não pode ser cobrada"):
            garantir_cobranca_pix(paga)
        mock_client.return_value.criar_cobranca.assert_called_once()

    def test_resposta_sem_pix_nao_grava_cobranca(self, mock_client):
        mock_client.return_value.criar_cobranca.return_value = Cobranca("c1", "ACTIVE", 12000, "", "", None, "", None)
        with self.assertRaises(WooviInvalidResponseError):
            garantir_cobranca_pix(self.mensalidade)
        self.assertFalse(CobrancaPix.objects.exists())

    def test_remover_tira_o_pix_do_ar(self, mock_client):
        cobranca = self.cobranca()
        remover_cobranca_pix(self.mensalidade)
        mock_client.return_value.remover_cobranca.assert_called_once_with(cobranca.correlation_id)
        cobranca.refresh_from_db()
        self.assertEqual(cobranca.status, CobrancaPix.CANCELADA)


class RegistrarPagamentoPixTests(CenarioWoovi, TestCase):
    def setUp(self):
        self.criar_cenario()

    def test_marca_paga_e_abre_repasse_pendente(self):
        cobranca = self.cobranca()

        self.assertTrue(registrar_pagamento_pix(cobranca, transaction_id="tx1"))

        self.mensalidade.refresh_from_db()
        self.assertEqual((self.mensalidade.status, self.mensalidade.forma_pagamento), ("paga", "pix"))
        cobranca.refresh_from_db()
        self.assertEqual((cobranca.status, cobranca.transaction_id), (CobrancaPix.PAGA, "tx1"))
        repasse = Repasse.objects.get()
        self.assertEqual((repasse.status, repasse.pix_key_destino, repasse.valor), (Repasse.PENDENTE, CHAVE, None))

    def test_guarda_a_taxa_e_o_liquido(self):
        cobranca = self.cobranca()

        registrar_pagamento_pix(cobranca, taxa_centavos=85, valor_pago_centavos=12000)

        cobranca.refresh_from_db()
        self.assertEqual((cobranca.taxa, cobranca.valor_liquido), (Decimal("0.85"), Decimal("119.15")))

    def test_sem_taxa_o_liquido_fica_vazio(self):
        cobranca = self.cobranca()
        registrar_pagamento_pix(cobranca)
        cobranca.refresh_from_db()
        self.assertIsNone(cobranca.valor_liquido)

    def test_pagamento_repetido_nao_gera_nada_novo(self):
        cobranca = self.cobranca()
        registrar_pagamento_pix(cobranca)
        self.assertFalse(registrar_pagamento_pix(cobranca))
        self.assertEqual(Repasse.objects.count(), 1)

    def test_dois_pagamentos_viram_um_unico_repasse(self):
        registrar_pagamento_pix(self.cobranca())
        registrar_pagamento_pix(self.cobranca(mensalidade=self.nova_mensalidade(date(2026, 10, 1)), correlation_id="c2"))
        self.assertEqual(Repasse.objects.count(), 1)

    def test_pagamento_durante_saque_marca_saldo_novo(self):
        repasse = Repasse.objects.create(
            academia=self.academia, conta_recebimento=self.conta, pix_key_destino=CHAVE, status=Repasse.PROCESSANDO,
        )
        registrar_pagamento_pix(self.cobranca())
        repasse.refresh_from_db()
        self.assertTrue(repasse.saldo_novo_pendente)
        self.assertEqual(Repasse.objects.count(), 1)

    def test_mensalidade_cancelada_nao_muda_mas_o_dinheiro_e_repassado(self):
        self.mensalidade.status = "cancelada"
        self.mensalidade.save()
        with self.assertLogs("gestao.alertas", level="ERROR"):
            registrar_pagamento_pix(self.cobranca())
        self.mensalidade.refresh_from_db()
        self.assertEqual(self.mensalidade.status, "cancelada")
        self.assertEqual(Repasse.objects.count(), 1)

    def test_pagamento_em_dobro_gera_alerta_e_repasse(self):
        self.mensalidade.status = "paga"
        self.mensalidade.save()
        with self.assertLogs("gestao.alertas", level="ERROR") as logs:
            registrar_pagamento_pix(self.cobranca())
        self.assertIn("dobro", logs.output[0])
        self.assertEqual(Repasse.objects.count(), 1)

    def test_solicitar_repasse_concorrente_reaproveita_o_aberto(self):
        primeiro = solicitar_repasse(self.conta)
        segundo = solicitar_repasse(self.conta)
        self.assertEqual(primeiro, segundo)


@patch("integracoes.woovi.services.WooviClient")
class ConferirPagamentoPixTests(CenarioWoovi, TestCase):
    def setUp(self):
        self.criar_cenario()

    def test_pix_pago_sem_webhook_e_registrado(self, mock_client):
        cobranca = self.cobranca()
        mock_client.return_value.obter_cobranca.return_value = Cobranca(
            cobranca.correlation_id, "COMPLETED", 12000, "", "", None, "tx9", timezone.now(),
        )
        self.assertTrue(conferir_pagamento_pix(self.mensalidade))
        self.mensalidade.refresh_from_db()
        self.assertEqual(self.mensalidade.status, "paga")
        self.assertEqual(Repasse.objects.count(), 1)

    def test_pix_expirado_no_provedor_e_marcado(self, mock_client):
        cobranca = self.cobranca()
        mock_client.return_value.obter_cobranca.return_value = Cobranca(
            cobranca.correlation_id, "EXPIRED", 12000, "", "", None, "", None,
        )
        self.assertFalse(conferir_pagamento_pix(self.mensalidade))
        cobranca.refresh_from_db()
        self.assertEqual(cobranca.status, CobrancaPix.EXPIRADA)

