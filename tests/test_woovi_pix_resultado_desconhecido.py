"""Criação de Pix com resultado desconhecido (timeout, 5xx, falha local
depois do Pix criado na Woovi). Sem chamadas reais: o WooviClient é
simulado e o executor de testes bloqueia a rede."""
from datetime import timedelta
from unittest.mock import patch

from django.db import IntegrityError, connection, transaction
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from financeiro.models import CobrancaPix, Mensalidade, OperacaoCobrancaPix
from integracoes.woovi.client import Cobranca
from integracoes.woovi.exceptions import (
    WooviNotFoundError, WooviRequestError, WooviTimeoutError, WooviUnavailableError,
)
from integracoes.woovi.services import PixEmProcessamento, garantir_cobranca_pix
from tests.woovi_base import CenarioWoovi, cobranca_criada


def remota(correlation_id, status="ACTIVE", valor=12000, **extra):
    return Cobranca(
        correlation_id=correlation_id, status=status, valor_centavos=valor,
        br_code="00020126PIX", link_pagamento="https://woovi.com/pay/x",
        expira_em=timezone.now() + timedelta(days=30), transaction_id=extra.get("transaction_id", ""),
        pago_em=extra.get("pago_em"), taxa_centavos=extra.get("taxa_centavos"),
    )


def envelhecer(operacao, segundos=600):
    """Simula um pedido abandonado (o processo que o fazia morreu)."""
    OperacaoCobrancaPix.objects.filter(pk=operacao.pk).update(
        atualizada_em=timezone.now() - timedelta(seconds=segundos),
    )


@patch("integracoes.woovi.services.WooviClient")
class ResultadoDesconhecidoTests(CenarioWoovi, TestCase):
    def setUp(self):
        self.criar_cenario()
        self.usar_conta_propria()

    def criar(self):
        return garantir_cobranca_pix(Mensalidade.objects.get(pk=self.mensalidade.pk))

    def falhar_com_timeout(self, client):
        client.criar_cobranca.side_effect = WooviTimeoutError("sem resposta")
        with self.assertRaises(WooviTimeoutError):
            self.criar()
        return OperacaoCobrancaPix.objects.get()

    def test_timeout_guarda_a_operacao_incerta_e_nenhum_pix(self, mock_client):
        operacao = self.falhar_com_timeout(mock_client.return_value)

        self.assertEqual(operacao.status, OperacaoCobrancaPix.INCERTA)
        self.assertEqual(operacao.tentativas, 1)
        enviado = mock_client.return_value.criar_cobranca.call_args.kwargs["correlation_id"]
        self.assertEqual(operacao.correlation_id, enviado)
        self.assertFalse(CobrancaPix.objects.exists())

    def test_apos_timeout_consulta_e_registra_o_pix_criado_sem_criar_outro(self, mock_client):
        client = mock_client.return_value
        operacao = self.falhar_com_timeout(client)
        client.obter_cobranca.side_effect = lambda cid: remota(cid)

        cobranca = self.criar()

        client.obter_cobranca.assert_called_once_with(operacao.correlation_id)
        client.criar_cobranca.assert_called_once()  # só o pedido original
        self.assertEqual(cobranca.correlation_id, operacao.correlation_id)
        self.assertEqual(cobranca.status, CobrancaPix.ATIVA)
        operacao.refresh_from_db()
        self.assertEqual(operacao.status, OperacaoCobrancaPix.CONCLUIDA)
        self.assertEqual(operacao.cobranca, cobranca)

    def test_apos_timeout_nao_encontrado_repete_com_o_mesmo_correlation_id(self, mock_client):
        client = mock_client.return_value
        operacao = self.falhar_com_timeout(client)
        client.obter_cobranca.side_effect = WooviNotFoundError("não encontrada", 404)
        client.criar_cobranca.side_effect = cobranca_criada

        cobranca = self.criar()

        ids = [c.kwargs["correlation_id"] for c in client.criar_cobranca.call_args_list]
        self.assertEqual(ids, [operacao.correlation_id, operacao.correlation_id])
        self.assertEqual(cobranca.correlation_id, operacao.correlation_id)
        self.assertEqual(CobrancaPix.objects.count(), 1)
        self.assertEqual(OperacaoCobrancaPix.objects.get().tentativas, 2)

    def test_apos_timeout_pix_ja_pago_recebe_a_baixa_sem_novo_pedido(self, mock_client):
        client = mock_client.return_value
        self.falhar_com_timeout(client)
        client.obter_cobranca.side_effect = lambda cid: remota(
            cid, status="COMPLETED", transaction_id="tx-1", pago_em=timezone.now(), taxa_centavos=85,
        )

        with self.assertRaisesMessage(ValueError, "paga não pode ser cobrada"):
            self.criar()

        client.criar_cobranca.assert_called_once()
        self.mensalidade.refresh_from_db()
        self.assertEqual(self.mensalidade.status, "paga")
        self.assertEqual(self.mensalidade.forma_pagamento, "pix")
        cobranca = CobrancaPix.objects.get()
        self.assertEqual(cobranca.status, CobrancaPix.PAGA)
        self.assertEqual(str(cobranca.taxa), "0.85")

    def test_apos_timeout_pix_expirado_libera_um_pedido_novo(self, mock_client):
        client = mock_client.return_value
        antiga = self.falhar_com_timeout(client)
        client.obter_cobranca.side_effect = lambda cid: remota(cid, status="EXPIRED")
        client.criar_cobranca.side_effect = cobranca_criada

        cobranca = self.criar()

        antiga.refresh_from_db()
        self.assertEqual(antiga.status, OperacaoCobrancaPix.EXPIRADA)
        self.assertNotEqual(cobranca.correlation_id, antiga.correlation_id)
        self.assertEqual(OperacaoCobrancaPix.objects.get(status=OperacaoCobrancaPix.CONCLUIDA).cobranca, cobranca)

    def test_consulta_sem_resposta_nao_cria_outro_pix(self, mock_client):
        client = mock_client.return_value
        operacao = self.falhar_com_timeout(client)
        client.obter_cobranca.side_effect = WooviUnavailableError("fora do ar", 503)

        with self.assertRaises(WooviUnavailableError):
            self.criar()

        client.criar_cobranca.assert_called_once()
        operacao.refresh_from_db()
        self.assertEqual(operacao.status, OperacaoCobrancaPix.INCERTA)
        self.assertFalse(CobrancaPix.objects.exists())

    def test_erro_5xx_tambem_e_resultado_desconhecido(self, mock_client):
        mock_client.return_value.criar_cobranca.side_effect = WooviUnavailableError("HTTP 502", 502)
        with self.assertRaises(WooviUnavailableError):
            self.criar()
        self.assertEqual(OperacaoCobrancaPix.objects.get().status, OperacaoCobrancaPix.INCERTA)

    def test_recusa_da_woovi_fecha_a_operacao_e_permite_novo_pedido(self, mock_client):
        client = mock_client.return_value
        client.criar_cobranca.side_effect = WooviRequestError("HTTP 400: valor inválido", 400)
        with self.assertRaises(WooviRequestError):
            self.criar()
        recusada = OperacaoCobrancaPix.objects.get()
        self.assertEqual(recusada.status, OperacaoCobrancaPix.NAO_CRIADA)

        client.criar_cobranca.side_effect = cobranca_criada
        cobranca = self.criar()

        client.obter_cobranca.assert_not_called()
        self.assertNotEqual(cobranca.correlation_id, recusada.correlation_id)

    def test_pedido_recente_em_andamento_nao_e_duplicado(self, mock_client):
        client = mock_client.return_value
        client.criar_cobranca.side_effect = cobranca_criada
        OperacaoCobrancaPix.objects.create(
            mensalidade=self.mensalidade, conta_recebimento=self.conta,
            correlation_id="mensalidade-x-em-andamento", valor="120.00", validade_segundos=60,
        )

        with self.assertRaises(PixEmProcessamento):
            self.criar()

        client.criar_cobranca.assert_not_called()
        client.obter_cobranca.assert_not_called()

    def test_pedido_pendente_abandonado_e_reconciliado(self, mock_client):
        client = mock_client.return_value
        operacao = OperacaoCobrancaPix.objects.create(
            mensalidade=self.mensalidade, conta_recebimento=self.conta,
            correlation_id="mensalidade-x-abandonado", valor="120.00", validade_segundos=60,
        )
        envelhecer(operacao)
        client.obter_cobranca.side_effect = lambda cid: remota(cid)

        cobranca = self.criar()

        self.assertEqual(cobranca.correlation_id, "mensalidade-x-abandonado")
        client.criar_cobranca.assert_not_called()

    def test_segundo_pedido_durante_a_chamada_nao_gera_outro_pix(self, mock_client):
        client = mock_client.return_value
        concorrente = {}

        def criar_enquanto_outro_pede(**kw):
            # Outro pedido (família e operador ao mesmo tempo) chega no meio
            # da chamada HTTP do primeiro.
            try:
                self.criar()
            except PixEmProcessamento as exc:
                concorrente["erro"] = exc
            return cobranca_criada(**kw)

        client.criar_cobranca.side_effect = criar_enquanto_outro_pede

        cobranca = self.criar()

        self.assertIn("erro", concorrente)
        client.criar_cobranca.assert_called_once()
        self.assertEqual(CobrancaPix.objects.get(), cobranca)

    def test_indice_impede_duas_operacoes_abertas(self, mock_client):
        dados = dict(mensalidade=self.mensalidade, conta_recebimento=self.conta, valor="120.00", validade_segundos=60)
        OperacaoCobrancaPix.objects.create(correlation_id="a", **dados)
        with self.assertRaises(IntegrityError), transaction.atomic():
            OperacaoCobrancaPix.objects.create(correlation_id="b", status=OperacaoCobrancaPix.INCERTA, **dados)

    def test_mensalidade_paga_durante_a_chamada_tira_o_pix_novo_do_ar(self, mock_client):
        client = mock_client.return_value

        def pagar_enquanto_cria(**kw):
            Mensalidade.objects.filter(pk=self.mensalidade.pk).update(status="paga")
            return cobranca_criada(**kw)

        client.criar_cobranca.side_effect = pagar_enquanto_cria

        with self.assertRaisesMessage(ValueError, "paga não pode ser cobrada"):
            self.criar()

        cobranca = CobrancaPix.objects.get()
        self.assertEqual(cobranca.status, CobrancaPix.CANCELADA)
        client.remover_cobranca.assert_called_once_with(cobranca.correlation_id)

    def test_pix_substituido_sai_do_ar_e_o_historico_fica(self, mock_client):
        client = mock_client.return_value
        client.criar_cobranca.side_effect = cobranca_criada
        antiga = self.cobranca(conta=self.conta, dias=0)
        CobrancaPix.objects.filter(pk=antiga.pk).update(expira_em=timezone.now() + timedelta(minutes=10))

        nova = self.criar()

        antiga.refresh_from_db()
        self.assertEqual(antiga.status, CobrancaPix.CANCELADA)
        client.remover_cobranca.assert_called_once_with(antiga.correlation_id)
        self.assertEqual(OperacaoCobrancaPix.objects.get().substitui, antiga)
        self.assertNotEqual(nova.pk, antiga.pk)


@patch("integracoes.woovi.services.WooviClient")
class SemTransacaoDuranteHttpTests(CenarioWoovi, TransactionTestCase):
    """Commits reais: confirma que a operação já está gravada quando a
    Woovi é chamada e que ela sobrevive ao rollback da gravação do Pix."""

    def setUp(self):
        self.criar_cenario()
        self.usar_conta_propria()

    def test_operacao_ja_gravada_e_nenhuma_transacao_aberta_durante_a_chamada(self, mock_client):
        visto = {}

        def criar(**kw):
            visto["em_transacao"] = connection.in_atomic_block
            visto["gravada"] = OperacaoCobrancaPix.objects.filter(
                correlation_id=kw["correlation_id"], status=OperacaoCobrancaPix.PENDENTE,
            ).exists()
            return cobranca_criada(**kw)

        mock_client.return_value.criar_cobranca.side_effect = criar

        garantir_cobranca_pix(self.mensalidade)

        self.assertEqual(visto, {"em_transacao": False, "gravada": True})

    def test_pix_criado_na_woovi_e_falha_local_reconcilia_sem_duplicar(self, mock_client):
        client = mock_client.return_value
        client.criar_cobranca.side_effect = cobranca_criada
        criar_original = CobrancaPix.objects.create

        with patch.object(CobrancaPix.objects, "create", side_effect=RuntimeError("banco caiu")):
            with self.assertRaises(RuntimeError):
                garantir_cobranca_pix(self.mensalidade)

        operacao = OperacaoCobrancaPix.objects.get()
        self.assertEqual(operacao.status, OperacaoCobrancaPix.INCERTA)
        self.assertIn("não gravado", operacao.erro)
        self.assertFalse(CobrancaPix.objects.exists())
        self.assertIs(CobrancaPix.objects.create.__func__, criar_original.__func__)

        client.obter_cobranca.side_effect = lambda cid: remota(cid)
        cobranca = garantir_cobranca_pix(Mensalidade.objects.get(pk=self.mensalidade.pk))

        client.criar_cobranca.assert_called_once()
        self.assertEqual(cobranca.correlation_id, operacao.correlation_id)
        self.assertEqual(CobrancaPix.objects.count(), 1)
