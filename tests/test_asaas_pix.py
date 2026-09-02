from datetime import date
from decimal import Decimal
from unittest.mock import Mock, patch

import requests
from django.test import SimpleTestCase, TestCase

from academias.models import Academia
from atletas.models import Atleta, Responsavel
from financeiro.models import Mensalidade
from integracoes.asaas.client import AsaasAPIError, AsaasClient
from integracoes.asaas.services import obter_pix_mensalidade
from matriculas.models import Matricula
from servicos.models import Servico


class ObterPixQrCodeClientTests(SimpleTestCase):
    def setUp(self):
        env = {
            "ASAAS_BASE_URL": "https://api-sandbox.asaas.com/v3/",
            "ASAAS_API_KEY": "chave-teste",
        }
        with patch.dict("os.environ", env):
            self.client = AsaasClient()

    @patch("integracoes.asaas.client.requests.get")
    def test_consulta_endpoint_pix_com_payment_id(self, mock_get):
        response = Mock(status_code=200)
        response.json.return_value = {
            "success": True,
            "encodedImage": "iVBORw0KGgo=",
            "payload": "00020126...br.gov.bcb.pix",
            "expirationDate": "2026-09-10 23:59:59",
        }
        mock_get.return_value = response

        resultado = self.client.obter_pix_qrcode("pay_123")

        self.assertEqual(resultado["payload"], "00020126...br.gov.bcb.pix")
        mock_get.assert_called_once_with(
            "https://api-sandbox.asaas.com/v3/payments/pay_123/pixQrCode",
            headers=self.client.headers,
            timeout=30,
        )
        response.raise_for_status.assert_called_once_with()

    @patch("integracoes.asaas.client.requests.get")
    def test_converte_erro_http_em_asaas_api_error(self, mock_get):
        response = Mock(status_code=500)
        response.raise_for_status.side_effect = requests.HTTPError("erro")
        mock_get.return_value = response

        with self.assertRaisesMessage(AsaasAPIError, "HTTP 500"):
            self.client.obter_pix_qrcode("pay_123")


class ObterPixMensalidadeTests(TestCase):
    def setUp(self):
        self.academia = Academia.objects.create(
            nome="Academia Teste",
            cnpj="11111111000111",
        )
        self.responsavel = Responsavel.objects.create(
            academia=self.academia,
            nome="Responsável Teste",
            cpf="12345678901",
            whatsapp="5524999999999",
        )
        self.atleta = Atleta.objects.create(
            academia=self.academia,
            nome="Atleta Teste",
            responsavel_financeiro=self.responsavel,
        )
        self.servico = Servico.objects.create(
            academia=self.academia,
            nome="Jiu-jitsu",
            valor_padrao=Decimal("150.00"),
        )
        self.matricula = Matricula.objects.create(
            academia=self.academia,
            atleta=self.atleta,
            servico=self.servico,
            valor_mensalidade=Decimal("135.50"),
            data_inicio=date(2026, 1, 1),
        )
        self.mensalidade = Mensalidade.objects.create(
            academia=self.academia,
            matricula=self.matricula,
            competencia=date(2026, 9, 1),
            valor=Decimal("135.50"),
            vencimento=date(2026, 9, 10),
            status="pendente",
            asaas_payment_id="pay_123",
        )

    @patch("integracoes.asaas.services.AsaasClient")
    def test_retorna_dados_pix_com_sucesso(self, mock_client_class):
        cliente = mock_client_class.return_value
        cliente.obter_pix_qrcode.return_value = {
            "success": True,
            "encodedImage": "iVBORw0KGgo=",
            "payload": "00020126...br.gov.bcb.pix",
            "expirationDate": "2026-09-10 23:59:59",
        }

        resultado = obter_pix_mensalidade(self.mensalidade)

        self.assertEqual(
            resultado,
            {
                "payment_id": "pay_123",
                "payload": "00020126...br.gov.bcb.pix",
                "qr_code_base64": "iVBORw0KGgo=",
                "expiracao": "2026-09-10 23:59:59",
            },
        )

    @patch("integracoes.asaas.services.AsaasClient")
    def test_usa_o_payment_id_da_mensalidade(self, mock_client_class):
        cliente = mock_client_class.return_value
        cliente.obter_pix_qrcode.return_value = {
            "encodedImage": "img",
            "payload": "codigo",
        }

        obter_pix_mensalidade(self.mensalidade)

        cliente.obter_pix_qrcode.assert_called_once_with("pay_123")

    @patch("integracoes.asaas.services.AsaasClient")
    def test_rejeita_mensalidade_sem_payment_id(self, mock_client_class):
        self.mensalidade.asaas_payment_id = ""
        self.mensalidade.save(update_fields=["asaas_payment_id"])

        with self.assertRaisesMessage(
            ValueError,
            "ainda não possui cobrança criada no Asaas",
        ):
            obter_pix_mensalidade(self.mensalidade)

        mock_client_class.assert_not_called()

    @patch("integracoes.asaas.services.AsaasClient")
    def test_propaga_erro_da_api(self, mock_client_class):
        cliente = mock_client_class.return_value
        cliente.obter_pix_qrcode.side_effect = AsaasAPIError(
            "A API do Asaas retornou o status HTTP 500."
        )

        with self.assertRaisesMessage(AsaasAPIError, "HTTP 500"):
            obter_pix_mensalidade(self.mensalidade)

    @patch("integracoes.asaas.services.AsaasClient")
    def test_resposta_sem_payload_retorna_none(self, mock_client_class):
        cliente = mock_client_class.return_value
        cliente.obter_pix_qrcode.return_value = {
            "encodedImage": "iVBORw0KGgo=",
            "expirationDate": "2026-09-10 23:59:59",
        }

        resultado = obter_pix_mensalidade(self.mensalidade)

        self.assertIsNone(resultado["payload"])
        self.assertEqual(resultado["qr_code_base64"], "iVBORw0KGgo=")

    @patch("integracoes.asaas.services.AsaasClient")
    def test_resposta_sem_qr_code_retorna_none(self, mock_client_class):
        cliente = mock_client_class.return_value
        cliente.obter_pix_qrcode.return_value = {
            "payload": "00020126...br.gov.bcb.pix",
            "expirationDate": "2026-09-10 23:59:59",
        }

        resultado = obter_pix_mensalidade(self.mensalidade)

        self.assertIsNone(resultado["qr_code_base64"])
        self.assertEqual(
            resultado["payload"],
            "00020126...br.gov.bcb.pix",
        )

    @patch("integracoes.asaas.services.AsaasClient")
    def test_nao_faz_chamadas_desnecessarias(self, mock_client_class):
        cliente = mock_client_class.return_value
        cliente.obter_pix_qrcode.return_value = {
            "encodedImage": "img",
            "payload": "codigo",
        }

        obter_pix_mensalidade(self.mensalidade)

        mock_client_class.assert_called_once_with()
        cliente.obter_pix_qrcode.assert_called_once_with("pay_123")
        cliente.criar_cobranca.assert_not_called()
        cliente.buscar_cliente_por_id.assert_not_called()
