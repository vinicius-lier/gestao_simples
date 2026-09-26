from datetime import date
from decimal import Decimal
from unittest.mock import Mock, patch

import requests
from django.test import SimpleTestCase

from integracoes.asaas.client import AsaasAPIError, AsaasClient


class BuscarClientePorIdTests(SimpleTestCase):
    def setUp(self):
        env = {
            "ASAAS_BASE_URL": "https://api.example.com/v3",
            "ASAAS_API_KEY": "chave-teste",
        }
        with patch.dict("os.environ", env):
            self.client = AsaasClient()

    @patch("integracoes.asaas.client.requests.get")
    def test_retorna_cliente_encontrado(self, mock_get):
        response = Mock(status_code=200)
        response.json.return_value = {
            "id": "cus_123",
            "name": "Maria da Silva",
        }
        mock_get.return_value = response

        resultado = self.client.buscar_cliente_por_id("cus_123")

        self.assertEqual(resultado["id"], "cus_123")
        mock_get.assert_called_once_with(
            "https://api.example.com/v3/customers/cus_123",
            headers=self.client.headers,
            timeout=30,
        )
        response.raise_for_status.assert_called_once_with()

    @patch("integracoes.asaas.client.requests.get")
    def test_retorna_none_quando_cliente_nao_existe(self, mock_get):
        response = Mock(status_code=404)
        mock_get.return_value = response

        resultado = self.client.buscar_cliente_por_id("cus_inexistente")

        self.assertIsNone(resultado)
        response.raise_for_status.assert_not_called()
        response.json.assert_not_called()

    @patch("integracoes.asaas.client.requests.get")
    def test_propaga_outros_erros_http(self, mock_get):
        response = Mock(status_code=500)
        response.raise_for_status.side_effect = requests.HTTPError(
            "Erro no Asaas"
        )
        mock_get.return_value = response

        with self.assertRaisesMessage(AsaasAPIError, "HTTP 500"):
            self.client.buscar_cliente_por_id("cus_123")

    @patch("integracoes.asaas.client.requests.get")
    def test_trata_erro_de_conexao_sem_expor_credenciais(self, mock_get):
        mock_get.side_effect = requests.ConnectionError("falha interna")

        with self.assertRaisesMessage(
            AsaasAPIError,
            "Não foi possível comunicar com a API do Asaas.",
        ) as contexto:
            self.client.buscar_cliente_por_id("cus_123")

        self.assertNotIn("chave-teste", str(contexto.exception))


class CriarCobrancaClientTests(SimpleTestCase):
    def setUp(self):
        env = {
            "ASAAS_BASE_URL": "https://api-sandbox.asaas.com/v3/",
            "ASAAS_API_KEY": "chave-teste",
        }
        with patch.dict("os.environ", env):
            self.client = AsaasClient()

    @patch("integracoes.asaas.client.requests.post")
    def test_envia_payload_de_cobranca_pix(self, mock_post):
        response = Mock(status_code=200)
        response.json.return_value = {"id": "pay_123"}
        mock_post.return_value = response

        resultado = self.client.criar_cobranca(
            customer="cus_123",
            billing_type="PIX",
            valor=Decimal("135.50"),
            vencimento=date(2026, 9, 10),
            descricao="Mensalidade 09/2026",
        )

        self.assertEqual(resultado, {"id": "pay_123"})
        mock_post.assert_called_once_with(
            "https://api-sandbox.asaas.com/v3/payments",
            headers=self.client.headers,
            json={
                "customer": "cus_123",
                "billingType": "PIX",
                "value": 135.5,
                "dueDate": "2026-09-10",
                "description": "Mensalidade 09/2026",
            },
            timeout=30,
        )
        response.raise_for_status.assert_called_once_with()


class RemoverCobrancaTests(SimpleTestCase):
    def setUp(self):
        env = {
            "ASAAS_BASE_URL": "https://api.example.com/v3",
            "ASAAS_API_KEY": "chave-teste",
        }
        with patch.dict("os.environ", env):
            self.client = AsaasClient()

    @patch("integracoes.asaas.client.requests.delete")
    def test_exclui_a_cobranca(self, mock_delete):
        response = Mock(status_code=200)
        response.json.return_value = {"deleted": True, "id": "pay_123"}
        mock_delete.return_value = response

        resultado = self.client.remover_cobranca("pay_123")

        self.assertTrue(resultado["deleted"])
        mock_delete.assert_called_once_with(
            "https://api.example.com/v3/payments/pay_123",
            headers=self.client.headers,
            timeout=30,
        )

    @patch("integracoes.asaas.client.requests.delete")
    def test_cobranca_inexistente_retorna_none(self, mock_delete):
        mock_delete.return_value = Mock(status_code=404)

        self.assertIsNone(self.client.remover_cobranca("pay_inexistente"))

    @patch("integracoes.asaas.client.requests.delete")
    def test_cobranca_ja_paga_propaga_erro(self, mock_delete):
        response = Mock(status_code=400)
        response.raise_for_status.side_effect = requests.HTTPError("400")
        mock_delete.return_value = response

        with self.assertRaises(AsaasAPIError):
            self.client.remover_cobranca("pay_pago")
