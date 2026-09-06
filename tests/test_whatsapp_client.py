from unittest.mock import Mock, patch

import requests
from django.test import SimpleTestCase

from integracoes.whatsapp.client import WhatsAppAPIError, WhatsAppClient


class WhatsAppClientInitTests(SimpleTestCase):
    @patch.dict("os.environ", {}, clear=True)
    def test_erro_sem_phone_number_id(self):
        with self.assertRaisesMessage(ValueError, "WHATSAPP_PHONE_NUMBER_ID"):
            WhatsAppClient()

    @patch.dict("os.environ", {"WHATSAPP_PHONE_NUMBER_ID": "123"}, clear=True)
    def test_erro_sem_access_token(self):
        with self.assertRaisesMessage(ValueError, "WHATSAPP_ACCESS_TOKEN"):
            WhatsAppClient()

    @patch.dict(
        "os.environ",
        {"WHATSAPP_PHONE_NUMBER_ID": "123", "WHATSAPP_ACCESS_TOKEN": "tok"},
        clear=True,
    )
    def test_usa_versao_padrao_quando_nao_configurada(self):
        cliente = WhatsAppClient()
        self.assertIn("/v21.0/", cliente.base_url)
        self.assertTrue(cliente.base_url.endswith("/123/messages"))


class EnviarTemplateTests(SimpleTestCase):
    def setUp(self):
        env = {
            "WHATSAPP_API_VERSION": "v21.0",
            "WHATSAPP_PHONE_NUMBER_ID": "1234567890",
            "WHATSAPP_ACCESS_TOKEN": "token-teste",
        }
        with patch.dict("os.environ", env, clear=True):
            self.client = WhatsAppClient()

    @patch("integracoes.whatsapp.client.requests.post")
    def test_envia_template_com_parametros(self, mock_post):
        response = Mock(status_code=200)
        response.json.return_value = {"messages": [{"id": "wamid.123"}]}
        mock_post.return_value = response

        resultado = self.client.enviar_template(
            "5521999999999", "acesso_portal", parametros=["Maria", "https://x.io/abc"]
        )

        self.assertEqual(resultado["messages"][0]["id"], "wamid.123")
        mock_post.assert_called_once_with(
            "https://graph.facebook.com/v21.0/1234567890/messages",
            headers={
                "Authorization": "Bearer token-teste",
                "Content-Type": "application/json",
            },
            json={
                "messaging_product": "whatsapp",
                "to": "5521999999999",
                "type": "template",
                "template": {
                    "name": "acesso_portal",
                    "language": {"code": "pt_BR"},
                    "components": [
                        {
                            "type": "body",
                            "parameters": [
                                {"type": "text", "text": "Maria"},
                                {"type": "text", "text": "https://x.io/abc"},
                            ],
                        }
                    ],
                },
            },
            timeout=30,
        )

    @patch("integracoes.whatsapp.client.requests.post")
    def test_template_sem_parametros_nao_envia_components(self, mock_post):
        response = Mock(status_code=200)
        response.json.return_value = {}
        mock_post.return_value = response

        self.client.enviar_template("5521999999999", "hello_world")

        payload = mock_post.call_args.kwargs["json"]
        self.assertNotIn("components", payload["template"])

    @patch("integracoes.whatsapp.client.requests.post")
    def test_propaga_erro_http_sem_expor_token(self, mock_post):
        response = Mock(status_code=401)
        response.raise_for_status.side_effect = requests.HTTPError("erro")
        mock_post.return_value = response

        with self.assertRaisesMessage(WhatsAppAPIError, "HTTP 401") as contexto:
            self.client.enviar_template("5521999999999", "acesso_portal")

        self.assertNotIn("token-teste", str(contexto.exception))

    @patch("integracoes.whatsapp.client.requests.post")
    def test_erro_de_conexao(self, mock_post):
        mock_post.side_effect = requests.ConnectionError("falha")

        with self.assertRaisesMessage(
            WhatsAppAPIError, "Não foi possível comunicar com a API do WhatsApp."
        ):
            self.client.enviar_texto("5521999999999", "oi")
