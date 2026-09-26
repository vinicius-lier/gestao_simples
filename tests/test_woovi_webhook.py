import json
from unittest.mock import patch

from django.test import SimpleTestCase

from integracoes.woovi.client import WooviAPIError


@patch("integracoes.woovi.views.confirmar_pagamento_pix")
class WebhookWooviTests(SimpleTestCase):
    def post(self, payload):
        corpo = payload if isinstance(payload, str) else json.dumps(payload)
        return self.client.post("/webhooks/woovi/", data=corpo, content_type="application/json")

    def test_charge_completed_confere_o_pagamento_pelo_correlation_id(self, mock_confirmar):
        resposta = self.post({
            "event": "OPENPIX:CHARGE_COMPLETED",
            "charge": {"correlationID": "mensalidade-1-abc", "status": "COMPLETED"},
        })

        self.assertEqual(resposta.status_code, 200)
        mock_confirmar.assert_called_once_with("mensalidade-1-abc")

    def test_post_de_teste_do_cadastro_do_webhook_responde_200(self, mock_confirmar):
        # Formato que a Woovi manda ao cadastrar/testar a URL.
        resposta = self.post({"data_criacao": "2024-01-23T20:32:14.429Z", "event": "OPENPIX:CHARGE_COMPLETED"})

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.json(), {})
        mock_confirmar.assert_not_called()

    def test_outros_eventos_sao_ignorados(self, mock_confirmar):
        resposta = self.post({"event": "OPENPIX:CHARGE_EXPIRED", "charge": {"correlationID": "x"}})

        self.assertEqual(resposta.status_code, 200)
        mock_confirmar.assert_not_called()

    def test_falha_ao_consultar_a_woovi_responde_503_para_reenvio(self, mock_confirmar):
        mock_confirmar.side_effect = WooviAPIError("fora do ar")

        with self.assertLogs("integracoes.woovi.views", level="ERROR"):
            resposta = self.post({"event": "OPENPIX:CHARGE_COMPLETED", "charge": {"correlationID": "x"}})

        self.assertEqual(resposta.status_code, 503)

    def test_get_responde_ok(self, mock_confirmar):
        self.assertEqual(self.client.get("/webhooks/woovi/").status_code, 200)

    def test_corpo_invalido_retorna_400(self, mock_confirmar):
        self.assertEqual(self.post("não é json").status_code, 400)
        self.assertEqual(self.post("[1, 2]").status_code, 400)

    def test_put_nao_e_permitido(self, mock_confirmar):
        self.assertEqual(self.client.put("/webhooks/woovi/").status_code, 405)
