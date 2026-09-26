from unittest.mock import Mock, patch

import requests
from django.test import SimpleTestCase, override_settings

from integracoes.woovi.client import WooviAPIError, WooviClient

CONFIG = {"WOOVI_BASE_URL": "https://api.woovi-sandbox.com/", "WOOVI_APP_ID": "app-id-teste"}


def resposta(status=200, json=None):
    r = Mock(status_code=status)
    r.json.return_value = json if json is not None else {}
    return r


@override_settings(**CONFIG)
class WooviClientTests(SimpleTestCase):
    def setUp(self):
        self.client = WooviClient()

    def test_autentica_com_o_app_id_cru_sem_bearer(self):
        self.assertEqual(self.client.headers["Authorization"], "app-id-teste")
        self.assertEqual(self.client.base_url, "https://api.woovi-sandbox.com")

    @override_settings(WOOVI_APP_ID="")
    def test_sem_app_id_levanta_value_error(self):
        with self.assertRaisesMessage(ValueError, "WOOVI_APP_ID"):
            WooviClient()

    @patch("integracoes.woovi.client.requests.request")
    def test_criar_cobranca_envia_payload_idempotente(self, mock_request):
        mock_request.return_value = resposta(json={"charge": {"correlationID": "c1", "brCode": "000201"}})

        cobranca = self.client.criar_cobranca(
            correlation_id="c1",
            valor_centavos=12000,
            comentario="Mensalidade 09/2026 - Ana",
            expira_em_segundos=3600,
            cliente={"name": "Maria", "taxID": "12345678900"},
        )

        self.assertEqual(cobranca["brCode"], "000201")
        args, kwargs = mock_request.call_args
        self.assertEqual(args, ("POST", "https://api.woovi-sandbox.com/api/v1/charge"))
        self.assertEqual(kwargs["params"], {"return_existing": "true"})
        self.assertEqual(kwargs["json"], {
            "correlationID": "c1",
            "value": 12000,
            "comment": "Mensalidade 09/2026 - Ana",
            "expiresIn": 3600,
            "customer": {"name": "Maria", "taxID": "12345678900"},
        })
        self.assertEqual(kwargs["headers"]["Authorization"], "app-id-teste")

    @patch("integracoes.woovi.client.requests.request")
    def test_obter_e_remover_codificam_o_correlation_id_na_url(self, mock_request):
        mock_request.return_value = resposta(json={"charge": {"status": "ACTIVE"}, "status": "OK"})

        self.client.obter_cobranca("a/b c")
        self.assertEqual(mock_request.call_args.args, ("GET", "https://api.woovi-sandbox.com/api/v1/charge/a%2Fb%20c"))

        self.client.remover_cobranca("c1")
        self.assertEqual(mock_request.call_args.args, ("DELETE", "https://api.woovi-sandbox.com/api/v1/charge/c1"))

    @patch("integracoes.woovi.client.requests.request")
    def test_erro_http_traz_a_mensagem_da_woovi(self, mock_request):
        mock_request.return_value = resposta(400, json={"error": "Cobrança já foi paga"})

        with self.assertRaisesMessage(WooviAPIError, "HTTP 400: Cobrança já foi paga"):
            self.client.remover_cobranca("c1")

    @patch("integracoes.woovi.client.requests.request")
    def test_erro_de_conexao_nao_expoe_credenciais(self, mock_request):
        mock_request.side_effect = requests.ConnectionError("app-id-teste vazou?")

        with self.assertRaises(WooviAPIError) as ctx:
            self.client.obter_cobranca("c1")

        self.assertNotIn("app-id-teste", str(ctx.exception))

    @patch("integracoes.woovi.client.requests.request")
    def test_resposta_sem_cobranca_e_erro(self, mock_request):
        mock_request.return_value = resposta(json={"outra": "coisa"})

        with self.assertRaises(WooviAPIError):
            self.client.obter_cobranca("c1")
