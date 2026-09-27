"""FASE 2A — webhook opcional de eventos de conexão da Evolution."""
import json

from django.test import TestCase, override_settings

from academias.models import Academia, IntegracaoWhatsApp
from financeiro.models import LembreteCobranca


@override_settings(EVOLUTION_WEBHOOK_REQUIRE_TOKEN=False, EVOLUTION_WEBHOOK_TOKEN="")
class WebhookEvolutionTests(TestCase):
    def setUp(self):
        self.academia = Academia.objects.create(nome="Keiko", cnpj="EWH1")
        self.config = IntegracaoWhatsApp.objects.create(
            academia=self.academia,
            provider=IntegracaoWhatsApp.PROVIDER_EVOLUTION,
            evolution_instance_name="keiko",
        )

    def _post(self, body, headers=None):
        return self.client.post(
            "/webhooks/evolution/",
            data=json.dumps(body),
            content_type="application/json",
            headers=headers or {},
        )

    def test_connection_update_open_marca_conectado(self):
        resp = self._post({
            "event": "connection.update",
            "instance": "keiko",
            "data": {"state": "open", "number": "5521999998888"},
        })
        self.assertEqual(resp.status_code, 200)
        self.config.refresh_from_db()
        self.assertEqual(self.config.status_conexao, IntegracaoWhatsApp.STATUS_CONECTADO)
        self.assertEqual(self.config.numero_whatsapp, "5521999998888")
        self.assertIsNotNone(self.config.ultima_conexao_em)

    def test_connection_update_close_marca_desconectado(self):
        resp = self._post({"event": "connection.update", "instance": "keiko", "data": {"state": "close"}})
        self.assertEqual(resp.status_code, 200)
        self.config.refresh_from_db()
        self.assertEqual(self.config.status_conexao, IntegracaoWhatsApp.STATUS_DESCONECTADO)

    def test_qrcode_updated_marca_aguardando(self):
        resp = self._post({"event": "qrcode.updated", "instance": "keiko", "data": {}})
        self.assertEqual(resp.status_code, 200)
        self.config.refresh_from_db()
        self.assertEqual(
            self.config.status_conexao, IntegracaoWhatsApp.STATUS_AGUARDANDO_QRCODE
        )

    def test_instancia_desconhecida_e_ignorada(self):
        resp = self._post({"event": "connection.update", "instance": "outra", "data": {"state": "open"}})
        self.assertEqual(resp.status_code, 200)
        self.assertJSONEqual(resp.content, {"ignorado": True})
        self.config.refresh_from_db()
        self.assertEqual(self.config.status_conexao, IntegracaoWhatsApp.STATUS_DESCONECTADO)

    def test_instancia_pela_url(self):
        resp = self.client.post(
            "/webhooks/evolution/keiko/",
            data=json.dumps({"event": "connection.update", "data": {"state": "open"}}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.config.refresh_from_db()
        self.assertEqual(self.config.status_conexao, IntegracaoWhatsApp.STATUS_CONECTADO)

    def test_get_nao_permitido(self):
        self.assertEqual(self.client.get("/webhooks/evolution/").status_code, 405)

    def test_corpo_invalido(self):
        resp = self.client.post(
            "/webhooks/evolution/", data="nao-json", content_type="application/json"
        )
        self.assertEqual(resp.status_code, 400)

    def test_nao_dispara_cobranca(self):
        self._post({"event": "connection.update", "instance": "keiko", "data": {"state": "open"}})
        self.assertEqual(LembreteCobranca.objects.count(), 0)


class WebhookEvolutionTokenTests(TestCase):
    def setUp(self):
        self.academia = Academia.objects.create(nome="Keiko", cnpj="EWH2")
        self.config = IntegracaoWhatsApp.objects.create(
            academia=self.academia,
            provider=IntegracaoWhatsApp.PROVIDER_EVOLUTION,
            evolution_instance_name="keiko",
        )

    def _post(self, headers=None):
        return self.client.post(
            "/webhooks/evolution/",
            data=json.dumps({"event": "connection.update", "instance": "keiko", "data": {"state": "open"}}),
            content_type="application/json",
            headers=headers or {},
        )

    @override_settings(EVOLUTION_WEBHOOK_REQUIRE_TOKEN=True, EVOLUTION_WEBHOOK_TOKEN="")
    def test_producao_sem_token_recusa_503(self):
        self.assertEqual(self._post().status_code, 503)

    @override_settings(EVOLUTION_WEBHOOK_REQUIRE_TOKEN=True, EVOLUTION_WEBHOOK_TOKEN="segredo")
    def test_token_errado_401(self):
        self.assertEqual(self._post(headers={"x-evolution-token": "nope"}).status_code, 401)
        self.config.refresh_from_db()
        self.assertEqual(self.config.status_conexao, IntegracaoWhatsApp.STATUS_DESCONECTADO)

    @override_settings(EVOLUTION_WEBHOOK_REQUIRE_TOKEN=True, EVOLUTION_WEBHOOK_TOKEN="segredo")
    def test_token_certo_200(self):
        resp = self._post(headers={"x-evolution-token": "segredo"})
        self.assertEqual(resp.status_code, 200)
        self.config.refresh_from_db()
        self.assertEqual(self.config.status_conexao, IntegracaoWhatsApp.STATUS_CONECTADO)
