from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from financeiro.models import CobrancaPix, ContaRecebimento, Repasse
from integracoes.woovi import repasses
from integracoes.woovi.client import LancamentoExtrato
from integracoes.woovi.exceptions import WooviAuthError, WooviTimeoutError, WooviUnavailableError
from tests.woovi_base import CHAVE, CenarioWoovi, saque, subconta


def lancamento(operacao, valor, minutos=0):
    return LancamentoExtrato(
        id=operacao, momento=timezone.now() + timedelta(minutes=minutos),
        operacao=operacao, valor_centavos=valor, saldo_centavos=None,
    )


@patch("integracoes.woovi.repasses.WooviClient")
class ProcessarRepasseTests(CenarioWoovi, TestCase):
    def setUp(self):
        self.criar_cenario()
        self.repasse = Repasse.objects.create(
            academia=self.academia, conta_recebimento=self.conta, pix_key_destino=CHAVE,
        )

    def processar(self):
        repasses.processar_repasse(self.repasse.pk)
        self.repasse.refresh_from_db()
        return self.repasse

    def vencer(self):
        Repasse.objects.filter(pk=self.repasse.pk).update(proxima_tentativa_em=timezone.now())

    def pix_pago(self, liquido=Decimal("1.15"), taxa=Decimal("0.85"), correlation_id="c-pago"):
        return self.cobranca(
            correlation_id=correlation_id, status=CobrancaPix.PAGA, pago_em=timezone.now(),
            taxa=taxa, valor_liquido=liquido,
        )

    # ------------------------------------------------ crédito do líquido
    def test_credita_o_liquido_na_subconta_antes_de_sacar(self, mock_client):
        api = mock_client.return_value
        api.obter_subconta.return_value = subconta(saldo=215)
        api.sacar_subconta.return_value = saque(115)
        cobranca = self.pix_pago()

        repasse = self.processar()

        api.creditar_subconta.assert_called_once_with(CHAVE, 115, descricao="Pix c-pago")
        cobranca.refresh_from_db()
        self.assertIsNotNone(cobranca.creditado_em)
        api.sacar_subconta.assert_called_once_with(CHAVE, 115)
        self.assertEqual(repasse.status, Repasse.PROCESSANDO)

    def test_pix_ja_creditado_nao_e_creditado_de_novo(self, mock_client):
        api = mock_client.return_value
        api.obter_subconta.return_value = subconta(saldo=0)
        cobranca = self.pix_pago()
        CobrancaPix.objects.filter(pk=cobranca.pk).update(creditado_em=timezone.now())
        self.processar()
        api.creditar_subconta.assert_not_called()

    def test_taxa_informada_depois_calcula_o_liquido(self, mock_client):
        api = mock_client.return_value
        api.obter_subconta.return_value = subconta(saldo=0)
        self.pix_pago(liquido=None, taxa=Decimal("0.85"))
        self.processar()
        # valor da cobrança de teste: R$ 120,00 − 0,85
        api.creditar_subconta.assert_called_once_with(CHAVE, 11915, descricao="Pix c-pago")

    def test_sem_taxa_requer_atencao_sem_creditar_nem_sacar(self, mock_client):
        api = mock_client.return_value
        self.pix_pago(liquido=None, taxa=None)
        with self.assertLogs("gestao.alertas", level="ERROR"):
            repasse = self.processar()
        self.assertEqual(repasse.status, Repasse.REQUER_ATENCAO)
        self.assertIn("taxa", repasse.erro)
        api.creditar_subconta.assert_not_called()
        api.sacar_subconta.assert_not_called()

    def test_liquido_zero_nao_chama_o_credito(self, mock_client):
        api = mock_client.return_value
        api.obter_subconta.return_value = subconta(saldo=0)
        cobranca = self.pix_pago(liquido=Decimal("0"))
        self.processar()
        api.creditar_subconta.assert_not_called()
        cobranca.refresh_from_db()
        self.assertIsNotNone(cobranca.creditado_em)

    def test_timeout_no_credito_confere_o_extrato_antes_de_repetir(self, mock_client):
        api = mock_client.return_value
        api.creditar_subconta.side_effect = WooviTimeoutError("sem resposta")
        cobranca = self.pix_pago()

        repasse = self.processar()
        self.assertEqual(repasse.status, Repasse.FALHA)
        cobranca.refresh_from_db()
        self.assertTrue(cobranca.credito_incerto)
        api.sacar_subconta.assert_not_called()

        # O crédito tinha entrado: aparece no extrato. Não pede de novo.
        api.extrato_subconta.return_value = [lancamento("CREDIT", 115)]
        api.obter_subconta.return_value = subconta(saldo=215)
        api.sacar_subconta.return_value = saque(115)
        self.vencer()
        self.processar()

        self.assertEqual(api.creditar_subconta.call_count, 1)
        cobranca.refresh_from_db()
        self.assertIsNotNone(cobranca.creditado_em)
        self.assertFalse(cobranca.credito_incerto)
        api.sacar_subconta.assert_called_once_with(CHAVE, 115)

    # --------------------------------------------------------- caminho feliz
    def test_consulta_o_saldo_real_e_saca_o_saldo_menos_a_tarifa(self, mock_client):
        # Saldo acumulado maior que uma mensalidade: saca o saldo, não os 120.
        # A tarifa de saque (R$ 1,00) sai do saldo além do valor pedido.
        mock_client.return_value.obter_subconta.return_value = subconta(saldo=23755)
        mock_client.return_value.sacar_subconta.return_value = saque(23655)

        repasse = self.processar()

        mock_client.return_value.obter_subconta.assert_called_once_with(CHAVE)
        mock_client.return_value.sacar_subconta.assert_called_once_with(CHAVE, 23655)
        self.assertEqual(repasse.status, Repasse.PROCESSANDO)
        self.assertEqual(repasse.valor, Decimal("236.55"))
        self.assertEqual(repasse.correlation_id, "saque-1")
        self.assertEqual(repasse.tentativas, 1)
        self.assertIsNotNone(repasse.processado_em)

    def test_saque_ja_confirmado_na_resposta_conclui(self, mock_client):
        mock_client.return_value.obter_subconta.return_value = subconta(saldo=12000)
        mock_client.return_value.sacar_subconta.return_value = saque(12000, status="CONFIRMED", e2e="E1")
        repasse = self.processar()
        self.assertEqual((repasse.status, repasse.end_to_end_id), (Repasse.CONCLUIDA, "E1"))

    def test_saque_a_partir_de_mil_reais_nao_tem_tarifa(self, mock_client):
        mock_client.return_value.obter_subconta.return_value = subconta(saldo=100000)
        mock_client.return_value.sacar_subconta.return_value = saque(100000)
        self.processar()
        mock_client.return_value.sacar_subconta.assert_called_once_with(CHAVE, 100000)

    def test_abaixo_de_mil_reais_desconta_a_tarifa(self, mock_client):
        mock_client.return_value.obter_subconta.return_value = subconta(saldo=99999)
        mock_client.return_value.sacar_subconta.return_value = saque(99899)
        self.processar()
        mock_client.return_value.sacar_subconta.assert_called_once_with(CHAVE, 99899)

    # ------------------------------------------------------------ sem saldo
    def test_saldo_zerado_aguarda_credito_e_depois_conclui_sem_saque(self, mock_client):
        mock_client.return_value.obter_subconta.return_value = subconta(saldo=0)

        for _ in range(repasses.TENTATIVAS_AGUARDANDO_SALDO - 1):
            repasse = self.processar()
            self.assertEqual(repasse.status, Repasse.PENDENTE)
            self.vencer()

        repasse = self.processar()
        self.assertEqual((repasse.status, repasse.valor), (Repasse.CONCLUIDA, Decimal("0")))
        mock_client.return_value.sacar_subconta.assert_not_called()

    def test_saldo_que_nao_cobre_tarifa_e_minimo_fica_para_o_proximo(self, mock_client):
        # Pix de teste de R$ 2,00: líquido R$ 1,15 na subconta. 1,15 − 1,00 de
        # tarifa fica abaixo do mínimo de R$ 1,01: não saca, segue guardado.
        mock_client.return_value.obter_subconta.return_value = subconta(saldo=115)
        repasse = self.processar()
        self.assertEqual((repasse.status, repasse.valor), (Repasse.CONCLUIDA, Decimal("0")))
        self.assertIn("tarifa", repasse.erro)
        mock_client.return_value.sacar_subconta.assert_not_called()

    def test_saldo_que_cobre_tarifa_e_minimo_saca(self, mock_client):
        mock_client.return_value.obter_subconta.return_value = subconta(saldo=201)
        mock_client.return_value.sacar_subconta.return_value = saque(101)
        self.processar()
        mock_client.return_value.sacar_subconta.assert_called_once_with(CHAVE, 101)

    def test_saldo_abaixo_do_minimo_nao_saca(self, mock_client):
        mock_client.return_value.obter_subconta.return_value = subconta(saldo=100)
        repasse = self.processar()
        self.assertEqual(repasse.status, Repasse.CONCLUIDA)
        self.assertIn("mínimo", repasse.erro)
        mock_client.return_value.sacar_subconta.assert_not_called()

    def test_chave_bloqueada_requer_atencao_na_hora(self, mock_client):
        mock_client.return_value.obter_subconta.return_value = subconta(saldo=12000, bloqueada=True)
        with self.assertLogs("gestao.alertas", level="ERROR"):
            repasse = self.processar()
        self.assertEqual(repasse.status, Repasse.REQUER_ATENCAO)
        self.assertTrue(ContaRecebimento.objects.get(pk=self.conta.pk).saque_bloqueado)
        mock_client.return_value.sacar_subconta.assert_not_called()

    # ------------------------------------------------------ falha e retry
    def test_falha_agenda_nova_tentativa_com_espera_crescente(self, mock_client):
        mock_client.return_value.obter_subconta.return_value = subconta(saldo=12000)
        mock_client.return_value.sacar_subconta.side_effect = WooviUnavailableError("fora", 503)

        esperas = []
        for _ in range(repasses.MAX_TENTATIVAS - 1):
            antes = timezone.now()
            repasse = self.processar()
            self.assertEqual(repasse.status, Repasse.FALHA)
            esperas.append(round((repasse.proxima_tentativa_em - antes).total_seconds() / 60))
            self.vencer()

        self.assertEqual(esperas, [1, 5, 15, 60, 180])

    def test_limite_de_tentativas_requer_atencao_e_alerta(self, mock_client):
        mock_client.return_value.obter_subconta.side_effect = WooviAuthError("HTTP 401", 401)
        for _ in range(repasses.MAX_TENTATIVAS - 1):
            self.processar()
            self.vencer()

        with self.assertLogs("gestao.alertas", level="ERROR") as logs:
            repasse = self.processar()

        self.assertEqual((repasse.status, repasse.tentativas), (Repasse.REQUER_ATENCAO, 6))
        self.assertIn("requer atenção", logs.output[0])
        # Deixa de ser "aberto": um novo pagamento pode abrir outro repasse.
        Repasse.objects.create(academia=self.academia, conta_recebimento=self.conta, pix_key_destino=CHAVE)

    def test_falha_nao_e_retentada_antes_da_hora(self, mock_client):
        mock_client.return_value.obter_subconta.side_effect = WooviUnavailableError("fora")
        self.processar()
        self.assertIsNone(repasses.processar_repasse(self.repasse.pk))
        self.assertEqual(mock_client.return_value.obter_subconta.call_count, 1)

    # ------------------------------------------------------------- timeout
    def test_timeout_reconcilia_o_extrato_antes_de_sacar_de_novo(self, mock_client):
        api = mock_client.return_value
        api.obter_subconta.return_value = subconta(saldo=12000)
        api.sacar_subconta.side_effect = WooviTimeoutError("sem resposta")

        repasse = self.processar()
        self.assertEqual(repasse.status, Repasse.FALHA)
        self.assertTrue(repasse.reconciliar)

        # O saque tinha saído: aparece no extrato (com a tarifa à parte).
        # Não pede de novo.
        api.extrato_subconta.return_value = [lancamento("WITHDRAWAL", 11900), lancamento("WITHDRAWAL_FEE", 100)]
        self.vencer()
        repasse = self.processar()

        self.assertEqual(repasse.status, Repasse.PROCESSANDO)
        self.assertFalse(repasse.reconciliar)
        self.assertEqual(api.sacar_subconta.call_count, 1)

    def test_saque_antigo_no_extrato_nao_conta(self, mock_client):
        api = mock_client.return_value
        api.obter_subconta.return_value = subconta(saldo=12000)
        api.sacar_subconta.side_effect = [WooviTimeoutError("sem resposta"), saque(12000)]
        self.processar()

        api.extrato_subconta.return_value = [lancamento("WITHDRAWAL", 11900, minutos=-60)]
        self.vencer()
        self.processar()

        self.assertEqual(api.sacar_subconta.call_count, 2)

    def test_timeout_sem_saque_no_extrato_consulta_saldo_e_saca(self, mock_client):
        api = mock_client.return_value
        api.obter_subconta.return_value = subconta(saldo=12000)
        api.sacar_subconta.side_effect = [WooviTimeoutError("sem resposta"), saque(12000)]
        self.processar()

        api.extrato_subconta.return_value = [lancamento("CREDIT", 12000)]
        self.vencer()
        repasse = self.processar()

        api.extrato_subconta.assert_called_once_with(CHAVE)
        self.assertEqual(api.obter_subconta.call_count, 2)
        self.assertEqual(api.sacar_subconta.call_count, 2)
        self.assertEqual(repasse.status, Repasse.PROCESSANDO)

    # --------------------------------------------------------- concorrência
    def test_dois_processos_nao_pegam_o_mesmo_repasse(self, mock_client):
        self.assertTrue(repasses.reivindicar(self.repasse.pk))
        self.assertFalse(repasses.reivindicar(self.repasse.pk))

    def test_repasse_em_andamento_nao_e_sacado_de_novo(self, mock_client):
        mock_client.return_value.obter_subconta.return_value = subconta(saldo=12000)
        mock_client.return_value.sacar_subconta.return_value = saque(12000)
        self.processar()
        self.assertIsNone(repasses.processar_repasse(self.repasse.pk))
        mock_client.return_value.sacar_subconta.assert_called_once()

    # --------------------------------------------------------- confirmação
    def test_confirmacao_conclui_e_abre_repasse_para_saldo_novo(self, mock_client):
        mock_client.return_value.obter_subconta.return_value = subconta(saldo=12000)
        mock_client.return_value.sacar_subconta.return_value = saque(12000)
        self.processar()
        Repasse.objects.filter(pk=self.repasse.pk).update(saldo_novo_pendente=True)

        repasses.confirmar_repasse("saque-1", end_to_end_id="E9")

        self.repasse.refresh_from_db()
        self.assertEqual((self.repasse.status, self.repasse.end_to_end_id), (Repasse.CONCLUIDA, "E9"))
        novo = Repasse.objects.exclude(pk=self.repasse.pk).get()
        self.assertEqual(novo.status, Repasse.PENDENTE)

    def test_confirmacao_repetida_e_idempotente(self, mock_client):
        Repasse.objects.filter(pk=self.repasse.pk).update(status=Repasse.CONCLUIDA, correlation_id="saque-1")
        self.assertIsNotNone(repasses.confirmar_repasse("saque-1"))
        self.assertEqual(Repasse.objects.count(), 1)

    def test_confirmacao_casa_por_destino_e_valor_sem_correlation(self, mock_client):
        Repasse.objects.filter(pk=self.repasse.pk).update(status=Repasse.PROCESSANDO, valor=Decimal("120.00"))
        repasse = repasses.confirmar_repasse("id-desconhecido", valor_centavos=12000, destino=CHAVE)
        self.assertEqual(repasse.pk, self.repasse.pk)
        self.repasse.refresh_from_db()
        self.assertEqual(self.repasse.status, Repasse.CONCLUIDA)

    def test_falha_informada_pelo_provedor_agenda_nova_tentativa(self, mock_client):
        Repasse.objects.filter(pk=self.repasse.pk).update(
            status=Repasse.PROCESSANDO, correlation_id="saque-1", tentativas=1,
        )
        repasses.falhar_repasse("saque-1", "PAY_PIX_KEY_ERROR")
        self.repasse.refresh_from_db()
        self.assertEqual(self.repasse.status, Repasse.FALHA)
        self.assertIn("PAY_PIX_KEY_ERROR", self.repasse.erro)

    # ------------------------------------------------ repasse sem confirmação
    def _parado(self):
        Repasse.objects.filter(pk=self.repasse.pk).update(
            status=Repasse.PROCESSANDO, valor=Decimal("120.00"), tentativas=1,
            processado_em=timezone.now() - timedelta(hours=1),
        )

    def test_sem_webhook_o_extrato_confirma(self, mock_client):
        self._parado()
        mock_client.return_value.extrato_subconta.return_value = [lancamento("WITHDRAWAL", 12000, minutos=-50)]
        repasses.conferir_repasse_em_andamento(self.repasse.pk)
        self.repasse.refresh_from_db()
        self.assertEqual(self.repasse.status, Repasse.CONCLUIDA)

    def test_sem_webhook_estorno_no_extrato_vira_falha(self, mock_client):
        self._parado()
        mock_client.return_value.extrato_subconta.return_value = [
            lancamento("WITHDRAWAL", 12000, minutos=-50), lancamento("WITHDRAWAL_REVERSAL", 12000, minutos=-40),
        ]
        repasses.conferir_repasse_em_andamento(self.repasse.pk)
        self.repasse.refresh_from_db()
        self.assertEqual(self.repasse.status, Repasse.FALHA)

    def test_comando_processa_devidos_e_confere_parados(self, mock_client):
        mock_client.return_value.obter_subconta.return_value = subconta(saldo=12000)
        mock_client.return_value.sacar_subconta.return_value = saque(12000)
        saida = StringIO()

        call_command("processar_repasses", stdout=saida)

        self.assertIn("1 repasse(s) processado(s)", saida.getvalue())
        self.repasse.refresh_from_db()
        self.assertEqual(self.repasse.status, Repasse.PROCESSANDO)
