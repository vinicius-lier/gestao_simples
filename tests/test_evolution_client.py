"""FASE 2A — camada HTTP da Evolution API (client + resolução de credencial)."""
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests
from django.test import SimpleTestCase, override_settings

from integracoes.evolution.client import (
    EvolutionAPIError,
    EvolutionClient,
    EvolutionConfigError,
    client_para_config,
    resolver_credencial,
)

BASE = "https://evo.example.com"
KEY = "super-secret-api-key"


def _config(**extra):
    base = dict(
        credencial_ref="EVOLUTION_API_KEY_TESTE",
        evolution_base_url=BASE,
        evolution_instance_name="keiko",
    )
    base.update(extra)
    return SimpleNamespace(**base)


class ResolverCredencialTests(SimpleTestCase):
    @override_settings()
    @patch.dict("os.environ", {"EVOLUTION_API_KEY_TESTE": KEY})
    def test_credencial_valida(self):
        self.assertEqual(resolver_credencial(_config()), KEY)

    @patch.dict("os.environ", {}, clear=True)
    def test_sem_credencial_ref(self):
        with self.assertRaisesMessage(EvolutionConfigError, "credencial_ref"):
            resolver_credencial(_config(credencial_ref=""))

    @patch.dict("os.environ", {}, clear=True)
    def test_variavel_de_ambiente_inexistente(self):
        with self.assertRaisesMessage(EvolutionConfigError, "EVOLUTION_API_KEY_TESTE"):
            resolver_credencial(_config())


class EvolutionClientInitTests(SimpleTestCase):
    def test_exige_base_url_instancia_e_key(self):
        with self.assertRaises(EvolutionConfigError):
            EvolutionClient("", "keiko", KEY)
        with self.assertRaises(EvolutionConfigError):
            EvolutionClient(BASE, "", KEY)
        with self.assertRaises(EvolutionConfigError):
            EvolutionClient(BASE, "keiko", "")

    @override_settings(EVOLUTION_TIMEOUT=15)
    def test_headers_e_timeout(self):
        client = EvolutionClient(BASE + "/", "keiko", KEY)
        self.assertEqual(client.base_url, BASE)  # sem barra final
        self.assertEqual(client.headers["apikey"], KEY)
        self.assertEqual(client.timeout, 15)


@override_settings(EVOLUTION_TIMEOUT=9)
class EvolutionClientRequestTests(SimpleTestCase):
    def setUp(self):
        self.client = EvolutionClient(BASE, "keiko", KEY)

    def _resp(self, status=200, body=None):
        r = Mock(status_code=status)
        r.json.return_value = body if body is not None else {}
        return r

    @patch("integracoes.evolution.client.requests.request")
    def test_criar_instancia(self, mock_req):
        mock_req.return_value = self._resp(body={"instance": {"instanceName": "keiko"}})

        self.client.criar_instancia(numero="5521999998888")

        metodo, url = mock_req.call_args.args
        self.assertEqual(metodo, "POST")
        self.assertEqual(url, f"{BASE}/instance/create")
        corpo = mock_req.call_args.kwargs["json"]
        self.assertEqual(corpo["instanceName"], "keiko")
        self.assertEqual(corpo["integration"], "WHATSAPP-BAILEYS")
        self.assertTrue(corpo["qrcode"])
        self.assertEqual(corpo["number"], "5521999998888")
        self.assertEqual(mock_req.call_args.kwargs["timeout"], 9)
        self.assertEqual(mock_req.call_args.kwargs["headers"]["apikey"], KEY)

    @patch("integracoes.evolution.client.requests.request")
    def test_buscar_estado_conexao(self, mock_req):
        mock_req.return_value = self._resp(body={"instance": {"state": "open"}})
        self.client.buscar_estado_conexao()
        metodo, url = mock_req.call_args.args
        self.assertEqual((metodo, url), ("GET", f"{BASE}/instance/connectionState/keiko"))

    @patch("integracoes.evolution.client.requests.request")
    def test_obter_qrcode(self, mock_req):
        mock_req.return_value = self._resp(body={"base64": "data:image/png;base64,AAA"})
        self.client.obter_qrcode()
        metodo, url = mock_req.call_args.args
        self.assertEqual((metodo, url), ("GET", f"{BASE}/instance/connect/keiko"))

    @patch("integracoes.evolution.client.requests.request")
    def test_logout(self, mock_req):
        mock_req.return_value = self._resp()
        self.client.logout()
        metodo, url = mock_req.call_args.args
        self.assertEqual((metodo, url), ("DELETE", f"{BASE}/instance/logout/keiko"))

    @patch("integracoes.evolution.client.requests.request")
    def test_deletar_instancia(self, mock_req):
        mock_req.return_value = self._resp()
        self.client.deletar_instancia()
        metodo, url = mock_req.call_args.args
        self.assertEqual((metodo, url), ("DELETE", f"{BASE}/instance/delete/keiko"))

    @patch("integracoes.evolution.client.requests.request")
    def test_enviar_texto(self, mock_req):
        mock_req.return_value = self._resp(body={"key": {"id": "3EB0"}})
        resp = self.client.enviar_texto("5521999998888", "Olá!")
        metodo, url = mock_req.call_args.args
        self.assertEqual((metodo, url), ("POST", f"{BASE}/message/sendText/keiko"))
        self.assertEqual(
            mock_req.call_args.kwargs["json"],
            {"number": "5521999998888", "text": "Olá!", "linkPreview": False},
        )
        self.assertEqual(resp["key"]["id"], "3EB0")

    @patch("integracoes.evolution.client.requests.request")
    def test_timeout_vira_erro_sanitizado(self, mock_req):
        mock_req.side_effect = requests.Timeout("timed out")
        with self.assertRaises(EvolutionAPIError) as ctx:
            self.client.buscar_estado_conexao()
        self.assertIn("Não foi possível comunicar", str(ctx.exception))
        self.assertNotIn(KEY, str(ctx.exception))
        self.assertNotIn(BASE, str(ctx.exception))

    @patch("integracoes.evolution.client.requests.request")
    def test_http_500_vira_erro_sem_vazar_segredo(self, mock_req):
        mock_req.return_value = self._resp(status=500)
        with self.assertRaises(EvolutionAPIError) as ctx:
            self.client.logout()
        self.assertIn("HTTP 500", str(ctx.exception))
        self.assertNotIn(KEY, str(ctx.exception))

    def test_sanitizar_remove_apikey_e_base_url(self):
        sujo = f"boom em {BASE}/instance/connect/keiko com {KEY}"
        limpo = self.client._sanitizar(sujo)
        self.assertNotIn(KEY, limpo)
        self.assertNotIn(BASE, limpo)

    @patch("integracoes.evolution.client.requests.request")
    def test_resposta_sem_json_vira_dict_vazio(self, mock_req):
        r = Mock(status_code=200)
        r.json.side_effect = ValueError("no json")
        mock_req.return_value = r
        self.assertEqual(self.client.logout(), {})


class ClientParaConfigTests(SimpleTestCase):
    def test_config_none(self):
        with self.assertRaises(EvolutionConfigError):
            client_para_config(None)

    @patch.dict("os.environ", {"EVOLUTION_API_KEY_TESTE": KEY})
    def test_monta_client_a_partir_da_config(self):
        client = client_para_config(_config())
        self.assertIsInstance(client, EvolutionClient)
        self.assertEqual(client.instance_name, "keiko")
        self.assertEqual(client._api_key, KEY)
