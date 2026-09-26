import json
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase

from financeiro.models import CobrancaPix, EventoWebhook, Repasse
from tests.test_woovi_assinatura import assinar, gerar_chave
from tests.woovi_base import CHAVE, CenarioWoovi

PRIVADA, PEM = gerar_chave()


def pago(correlation_id="mensalidade-1-abc", e2e="E2E1"):
    return {
        "event": "OPENPIX:CHARGE_COMPLETED",
        "charge": {
            "correlationID": correlation_id, "status": "COMPLETED", "value": 12000,
            "transactionID": "tx1", "paidAt": "2026-09-08T15:07:50.891Z",
            "customer": {"name": "Maria", "taxID": {"taxID": "52998224725", "type": "BR:CPF"}},
        },
        "pix": {
            "endToEndId": e2e, "value": 12000, "time": "2026-09-08T15:07:50.891Z",
            "payer": {"name": "Maria da Silva", "taxID": {"taxID": "52998224725"}},
        },
    }


@patch("integracoes.woovi.assinatura._chaves", return_value=[PEM])
class WebhookWooviTests(CenarioWoovi, TestCase):
    def setUp(self):
        self.criar_cenario()
        self.cobranca_pix = self.cobranca()

    def post(self, payload, assinado=True):
        corpo = (payload if isinstance(payload, str) else json.dumps(payload)).encode()
        headers = {"HTTP_X_WEBHOOK_SIGNATURE": assinar(PRIVADA, corpo)} if assinado else {}
        return self.client.post("/webhooks/woovi/", data=corpo, content_type="application/json", **headers)

    # ---------------------------------------------------------- segurança
    def test_sem_assinatura_e_recusado_sem_efeito(self, _chaves):
        resposta = self.post(pago(), assinado=False)
        self.assertEqual(resposta.status_code, 401)
        self.mensalidade.refresh_from_db()
        self.assertEqual(self.mensalidade.status, "pendente")
        self.assertFalse(EventoWebhook.objects.exists())

    def test_assinatura_de_outro_corpo_e_recusada(self, _chaves):
        corpo = json.dumps(pago()).encode()
        resposta = self.client.post(
            "/webhooks/woovi/", data=corpo.replace(b"12000", b"99999"), content_type="application/json",
            HTTP_X_WEBHOOK_SIGNATURE=assinar(PRIVADA, corpo),
        )
        self.assertEqual(resposta.status_code, 401)

    def test_post_de_teste_do_cadastro_responde_200(self, _chaves):
        resposta = self.post({"data_criacao": "2024-01-23T20:32:14.429Z", "event": "OPENPIX:CHARGE_COMPLETED"}, assinado=False)
        self.assertEqual((resposta.status_code, resposta.json()), (200, {}))

    # ------------------------------------------------------------- pagamento
    @patch("integracoes.woovi.repasses.WooviClient")
    @patch("integracoes.woovi.services.WooviClient")
    def test_pix_pago_registra_pagamento_e_abre_repasse_sem_sacar(self, servicos, job, _chaves):
        resposta = self.post(pago())

        self.assertEqual(resposta.status_code, 200)
        self.mensalidade.refresh_from_db()
        self.assertEqual((self.mensalidade.status, self.mensalidade.forma_pagamento), ("paga", "pix"))
        self.assertEqual(self.mensalidade.pago_em.day, 8)
        repasse = Repasse.objects.get()
        self.assertEqual((repasse.status, repasse.pix_key_destino), (Repasse.PENDENTE, CHAVE))
        # O webhook nunca chama o provedor: nem saldo, nem saque.
        servicos.assert_not_called()
        job.assert_not_called()

    def test_evento_gravado_sem_dados_pessoais_do_pagador(self, _chaves):
        self.post(pago())
        evento = EventoWebhook.objects.get()
        self.assertEqual(evento.status, EventoWebhook.PROCESSADO)
        self.assertEqual(evento.correlation_id, "mensalidade-1-abc")
        texto = json.dumps(evento.payload)
        self.assertNotIn("52998224725", texto)
        self.assertNotIn("Maria", texto)

    def test_webhook_duplicado_nao_gera_pagamento_nem_repasse_em_dobro(self, _chaves):
        self.post(pago())
        resposta = self.post(pago())
        self.assertEqual(resposta.status_code, 200)
        self.assertTrue(resposta.json().get("duplicado"))
        self.assertEqual(Repasse.objects.count(), 1)
        self.assertEqual(EventoWebhook.objects.count(), 1)

    def test_cobranca_inexistente_responde_200_e_registra_o_motivo(self, _chaves):
        resposta = self.post(pago(correlation_id="nao-existe"))
        self.assertEqual(resposta.status_code, 200)
        evento = EventoWebhook.objects.get()
        self.assertEqual((evento.status, evento.erro), (EventoWebhook.IGNORADO, "cobrança não encontrada"))
        self.assertFalse(Repasse.objects.exists())

    def test_cobranca_ja_paga_responde_200_sem_novo_repasse(self, _chaves):
        CobrancaPix.objects.filter(pk=self.cobranca_pix.pk).update(status=CobrancaPix.PAGA)
        resposta = self.post(pago(e2e="OUTRO"))
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(EventoWebhook.objects.get().erro, "pagamento já registrado")
        self.assertFalse(Repasse.objects.exists())

    def test_mensalidade_cancelada_nao_quebra(self, _chaves):
        self.mensalidade.status = "cancelada"
        self.mensalidade.save()
        with self.assertLogs("gestao.alertas", level="ERROR"):
            resposta = self.post(pago())
        self.assertEqual(resposta.status_code, 200)
        self.mensalidade.refresh_from_db()
        self.assertEqual(self.mensalidade.status, "cancelada")

    # ----------------------------------------------------------------- saque
    def _repasse_processando(self):
        return Repasse.objects.create(
            academia=self.academia, conta_recebimento=self.conta, pix_key_destino=CHAVE,
            status=Repasse.PROCESSANDO, correlation_id="saque-1", valor=Decimal("120.00"), tentativas=1,
        )

    def test_saque_confirmado_conclui_o_repasse(self, _chaves):
        repasse = self._repasse_processando()
        resposta = self.post({
            "event": "OPENPIX:MOVEMENT_CONFIRMED",
            "payment": {"value": 12000, "status": "CONFIRMED", "destinationAlias": CHAVE, "correlationID": "saque-1"},
            "transaction": {"value": 12000, "endToEndId": "E-SAQUE", "time": "2026-09-08T15:08:00Z"},
        })
        self.assertEqual(resposta.status_code, 200)
        repasse.refresh_from_db()
        self.assertEqual((repasse.status, repasse.end_to_end_id), (Repasse.CONCLUIDA, "E-SAQUE"))

    def test_saque_com_falha_agenda_nova_tentativa(self, _chaves):
        repasse = self._repasse_processando()
        self.post({
            "event": "OPENPIX:MOVEMENT_FAILED",
            "payment": {"value": 12000, "status": "FAILED", "destinationAlias": CHAVE, "correlationID": "saque-1"},
            "transaction": {"value": 12000, "endToEndId": "E-SAQUE"},
            "error": {"code": "PAY_PIX_KEY_ERROR", "description": "Falha ao Pagar Chave Pix"},
        })
        repasse.refresh_from_db()
        self.assertEqual(repasse.status, Repasse.FALHA)
        self.assertIn("Falha ao Pagar Chave Pix", repasse.erro)

    def test_saque_desconhecido_responde_200(self, _chaves):
        resposta = self.post({"event": "OPENPIX:MOVEMENT_CONFIRMED", "payment": {"correlationID": "nao-existe"}})
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(EventoWebhook.objects.get().erro, "repasse não encontrado")

    # --------------------------------------------------------------- formato
    def test_outros_eventos_sao_ignorados(self, _chaves):
        self.assertEqual(self.post({"event": "OPENPIX:CHARGE_EXPIRED", "charge": {"correlationID": "x"}}).status_code, 200)
        self.assertFalse(EventoWebhook.objects.exists())

    def test_corpo_invalido_retorna_400(self, _chaves):
        self.assertEqual(self.post("não é json").status_code, 400)
        self.assertEqual(self.post("[1, 2]").status_code, 400)

    def test_get_responde_ok_e_put_nao_e_permitido(self, _chaves):
        self.assertEqual(self.client.get("/webhooks/woovi/").status_code, 200)
        self.assertEqual(self.client.put("/webhooks/woovi/").status_code, 405)
