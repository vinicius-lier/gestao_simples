from unittest.mock import patch

from django.test import TestCase

from academias.models import Academia
from atletas.models import Responsavel
from integracoes.asaas.services import sincronizar_responsavel_asaas


class SincronizarResponsavelAsaasTestCase(TestCase):

    def setUp(self):
        self.academia = Academia.objects.create(
            nome="Academia Teste",
            cnpj="11111111000111",
        )

        self.responsavel = Responsavel.objects.create(
            academia=self.academia,
            nome="Maria da Silva",
            cpf="12345678901",
            whatsapp="5524999999999",
            email="maria@example.com",
        )

    @patch("integracoes.asaas.services.AsaasClient")
    def test_reutiliza_customer_id_existente(self, mock_asaas):
        self.responsavel.asaas_customer_id = "cus_existente"
        self.responsavel.save()

        customer_id = sincronizar_responsavel_asaas(
            self.responsavel
        )

        self.assertEqual(
            customer_id,
            "cus_existente",
        )

        mock_asaas.assert_not_called()

    def test_exige_cpf_do_responsavel(self):
        self.responsavel.cpf = ""
        self.responsavel.save()

        with self.assertRaises(ValueError):
            sincronizar_responsavel_asaas(
                self.responsavel
            )

    @patch("integracoes.asaas.services.AsaasClient")
    def test_reutiliza_cliente_que_ja_existe_no_asaas(
        self,
        mock_asaas,
    ):
        cliente = mock_asaas.return_value

        cliente.buscar_cliente_por_cpf_cnpj.return_value = {
            "id": "cus_encontrado"
        }

        customer_id = sincronizar_responsavel_asaas(
            self.responsavel
        )

        self.assertEqual(
            customer_id,
            "cus_encontrado",
        )

        self.responsavel.refresh_from_db()

        self.assertEqual(
            self.responsavel.asaas_customer_id,
            "cus_encontrado",
        )

        cliente.criar_cliente.assert_not_called()

    @patch("integracoes.asaas.services.AsaasClient")
    def test_cria_cliente_quando_nao_existe_no_asaas(
        self,
        mock_asaas,
    ):
        cliente = mock_asaas.return_value

        cliente.buscar_cliente_por_cpf_cnpj.return_value = None

        cliente.criar_cliente.return_value = {
            "id": "cus_novo"
        }

        customer_id = sincronizar_responsavel_asaas(
            self.responsavel
        )

        self.assertEqual(
            customer_id,
            "cus_novo",
        )

        self.responsavel.refresh_from_db()

        self.assertEqual(
            self.responsavel.asaas_customer_id,
            "cus_novo",
        )

        cliente.criar_cliente.assert_called_once_with(
            nome="Maria da Silva",
            cpf_cnpj="12345678901",
            telefone="5524999999999",
            email="maria@example.com",
        )