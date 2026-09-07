"""SEGURANÇA ASAAS — em produção o webhook exige token configurado."""
import json
from datetime import date
from decimal import Decimal

from django.test import TestCase, override_settings

from academias.models import Academia
from atletas.models import Atleta
from financeiro.models import Mensalidade
from matriculas.models import Matricula
from modalidades.models import Modalidade


class WebhookAsaasProducaoTests(TestCase):
    def setUp(self):
        self.academia = Academia.objects.create(nome="Ac", cnpj="WHP1")
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

    def _post(self, headers=None):
        return self.client.post(
            "/webhooks/asaas/",
            data=json.dumps({"event": "PAYMENT_RECEIVED", "payment": {"id": "pay_123"}}),
            content_type="application/json",
            headers=headers or {},
        )

    @override_settings(ASAAS_WEBHOOK_REQUIRE_TOKEN=True, ASAAS_WEBHOOK_TOKEN="")
    def test_producao_sem_token_recusa_post_com_503(self):
        resposta = self._post()
        self.assertEqual(resposta.status_code, 503)
        self.mensalidade.refresh_from_db()
        self.assertEqual(self.mensalidade.status, "pendente")

    @override_settings(ASAAS_WEBHOOK_REQUIRE_TOKEN=True, ASAAS_WEBHOOK_TOKEN="")
    def test_producao_sem_token_ainda_responde_get(self):
        # o cadastro de webhook do Asaas checa a URL com GET antes de salvar
        self.assertEqual(self.client.get("/webhooks/asaas/").status_code, 200)

    @override_settings(ASAAS_WEBHOOK_REQUIRE_TOKEN=True, ASAAS_WEBHOOK_TOKEN="segredo")
    def test_producao_com_token_configurado_funciona(self):
        resposta = self._post(headers={"asaas-access-token": "segredo"})
        self.assertEqual(resposta.status_code, 200)
        self.mensalidade.refresh_from_db()
        self.assertEqual(self.mensalidade.status, "paga")

    @override_settings(ASAAS_WEBHOOK_REQUIRE_TOKEN=True, ASAAS_WEBHOOK_TOKEN="segredo")
    def test_producao_com_token_configurado_rejeita_header_errado(self):
        resposta = self._post(headers={"asaas-access-token": "errado"})
        self.assertEqual(resposta.status_code, 401)

    @override_settings(ASAAS_WEBHOOK_REQUIRE_TOKEN=False, ASAAS_WEBHOOK_TOKEN="")
    def test_dev_sem_token_mantem_comportamento_permissivo(self):
        resposta = self._post()
        self.assertEqual(resposta.status_code, 200)
        self.mensalidade.refresh_from_db()
        self.assertEqual(self.mensalidade.status, "paga")
