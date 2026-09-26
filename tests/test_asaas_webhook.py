import json
from datetime import date
from decimal import Decimal

from django.test import TestCase, override_settings

from academias.models import Academia
from atletas.models import Atleta
from financeiro.models import Mensalidade
from matriculas.models import Matricula
from modalidades.models import Modalidade


@override_settings(ASAAS_WEBHOOK_TOKEN="")
class WebhookAsaasTests(TestCase):
    """Sem token por padrão nestes testes — independente do .env local
    de quem está rodando a suíte."""

    def setUp(self):
        self.academia = Academia.objects.create(nome="Academia Teste", cnpj="WH1")
        self.atleta = Atleta.objects.create(academia=self.academia, nome="Atleta")
        self.modalidade = Modalidade.objects.create(academia=self.academia, nome="Judô")
        self.matricula = Matricula.objects.create(
            academia=self.academia, atleta=self.atleta, modalidade=self.modalidade,
            valor_mensalidade=Decimal("120.00"), dia_vencimento=10, data_inicio=date(2026, 1, 1),
        )
        self.mensalidade = Mensalidade.objects.create(
            academia=self.academia, matricula=self.matricula, competencia=date(2026, 9, 1),
            valor=Decimal("120.00"), vencimento=date(2026, 9, 10), status="pendente",
            asaas_payment_id="pay_123",
        )

    def post(self, payload):
        return self.client.post(
            "/webhooks/asaas/", data=json.dumps(payload), content_type="application/json"
        )

    def test_payment_received_marca_mensalidade_como_paga(self):
        resposta = self.post({"event": "PAYMENT_RECEIVED", "payment": {"id": "pay_123", "billingType": "PIX"}})
        self.assertEqual(resposta.status_code, 200)
        self.mensalidade.refresh_from_db()
        self.assertEqual(self.mensalidade.status, "paga")
        self.assertEqual(self.mensalidade.forma_pagamento, "pix")
        self.assertIsNotNone(self.mensalidade.pago_em)

    def test_aceita_payment_id_no_nivel_raiz(self):
        resposta = self.post({"event": "PAYMENT_CONFIRMED", "payment_id": "pay_123"})
        self.assertEqual(resposta.status_code, 200)
        self.mensalidade.refresh_from_db()
        self.assertEqual(self.mensalidade.status, "paga")

    def test_evento_irrelevante_nao_altera_nada(self):
        resposta = self.post({"event": "PAYMENT_OVERDUE", "payment": {"id": "pay_123"}})
        self.assertEqual(resposta.status_code, 200)
        self.mensalidade.refresh_from_db()
        self.assertEqual(self.mensalidade.status, "pendente")

    def test_payment_id_desconhecido_nao_quebra(self):
        resposta = self.post({"event": "PAYMENT_RECEIVED", "payment": {"id": "pay_inexistente"}})
        self.assertEqual(resposta.status_code, 200)
        self.mensalidade.refresh_from_db()
        self.assertEqual(self.mensalidade.status, "pendente")

    def test_pagamento_de_mensalidade_cancelada_responde_200_e_registra_aviso(self):
        # 5xx faria o Asaas reenviar e, com falhas seguidas, pausar a fila.
        self.mensalidade.status = "cancelada"
        self.mensalidade.save(update_fields=["status"])

        with self.assertLogs("integracoes.asaas.views", level="WARNING") as logs:
            resposta = self.post({"event": "PAYMENT_RECEIVED", "payment": {"id": "pay_123", "billingType": "PIX"}})

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("pay_123", logs.output[0])
        self.mensalidade.refresh_from_db()
        self.assertEqual(self.mensalidade.status, "cancelada")

    def test_mensalidade_ja_paga_nao_e_reprocessada(self):
        self.mensalidade.status = "paga"
        self.mensalidade.pago_em = None
        self.mensalidade.save(update_fields=["status", "pago_em"])
        self.post({"event": "PAYMENT_RECEIVED", "payment": {"id": "pay_123"}})
        self.mensalidade.refresh_from_db()
        self.assertIsNone(self.mensalidade.pago_em)

    def test_get_responde_ok_sem_exigir_token(self):
        # O formulário de webhook do Asaas testa a URL com GET antes de
        # salvar; precisa responder 2xx mesmo sem o cabeçalho do token.
        resposta = self.client.get("/webhooks/asaas/")
        self.assertEqual(resposta.status_code, 200)

    def test_put_nao_e_permitido(self):
        self.assertEqual(self.client.put("/webhooks/asaas/").status_code, 405)

    def test_corpo_invalido_retorna_400(self):
        resposta = self.client.post("/webhooks/asaas/", data="não é json", content_type="application/json")
        self.assertEqual(resposta.status_code, 400)

    @override_settings(ASAAS_WEBHOOK_TOKEN="segredo")
    def test_get_ignora_token_mesmo_configurado(self):
        self.assertEqual(self.client.get("/webhooks/asaas/").status_code, 200)

    @override_settings(ASAAS_WEBHOOK_TOKEN="segredo")
    def test_token_invalido_e_rejeitado(self):
        resposta = self.post({"event": "PAYMENT_RECEIVED", "payment": {"id": "pay_123"}})
        self.assertEqual(resposta.status_code, 401)
        self.mensalidade.refresh_from_db()
        self.assertEqual(self.mensalidade.status, "pendente")

    @override_settings(ASAAS_WEBHOOK_TOKEN="segredo")
    def test_token_valido_e_aceito(self):
        resposta = self.client.post(
            "/webhooks/asaas/",
            data=json.dumps({"event": "PAYMENT_RECEIVED", "payment": {"id": "pay_123"}}),
            content_type="application/json",
            headers={"asaas-access-token": "segredo"},
        )
        self.assertEqual(resposta.status_code, 200)
        self.mensalidade.refresh_from_db()
        self.assertEqual(self.mensalidade.status, "paga")
