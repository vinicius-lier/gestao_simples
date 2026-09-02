from unittest.mock import patch

from django.test import TestCase

from academias.models import Academia
from atletas.models import Responsavel
from integracoes.asaas.client import AsaasAPIError
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
    def test_reutiliza_customer_id_existente_e_valido(self, mock_asaas):
        self.responsavel.asaas_customer_id = "cus_existente"
        self.responsavel.save()
        cliente = mock_asaas.return_value
        cliente.buscar_cliente_por_id.return_value = {
            "id": "cus_existente"
        }

        customer_id = sincronizar_responsavel_asaas(
            self.responsavel
        )

        self.assertEqual(
            customer_id,
            "cus_existente",
        )

        cliente.buscar_cliente_por_id.assert_called_once_with(
            "cus_existente"
        )
        cliente.buscar_cliente_por_cpf_cnpj.assert_not_called()
        cliente.criar_cliente.assert_not_called()

    @patch("integracoes.asaas.services.AsaasClient")
    def test_descarta_id_invalido_e_reutiliza_cliente_por_cpf(
        self,
        mock_asaas,
    ):
        self.responsavel.asaas_customer_id = "cus_invalido"
        self.responsavel.save()
        cliente = mock_asaas.return_value
        cliente.buscar_cliente_por_id.return_value = None
        cliente.buscar_cliente_por_cpf_cnpj.return_value = {
            "id": "cus_correto"
        }

        customer_id = sincronizar_responsavel_asaas(self.responsavel)

        self.assertEqual(customer_id, "cus_correto")
        self.responsavel.refresh_from_db()
        self.assertEqual(
            self.responsavel.asaas_customer_id,
            "cus_correto",
        )
        cliente.buscar_cliente_por_cpf_cnpj.assert_called_once_with(
            "12345678901"
        )
        cliente.criar_cliente.assert_not_called()

    def test_exige_cpf_do_responsavel(self):
        self.responsavel.cpf = ""
        self.responsavel.save()

        with self.assertRaises(ValueError):
            sincronizar_responsavel_asaas(
                self.responsavel
            )

    @patch("integracoes.asaas.services.AsaasClient")
    def test_nao_consulta_api_quando_responsavel_nao_tem_cpf(
        self,
        mock_asaas,
    ):
        self.responsavel.cpf = ""
        self.responsavel.asaas_customer_id = "cus_existente"
        self.responsavel.save()

        with self.assertRaisesMessage(
            ValueError,
            "O responsável precisa possuir CPF",
        ):
            sincronizar_responsavel_asaas(self.responsavel)

        mock_asaas.assert_not_called()

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

    @patch("integracoes.asaas.services.AsaasClient")
    def test_propaga_erro_da_api_sem_alterar_id_valido_localmente(
        self,
        mock_asaas,
    ):
        self.responsavel.asaas_customer_id = "cus_existente"
        self.responsavel.save()
        cliente = mock_asaas.return_value
        cliente.buscar_cliente_por_id.side_effect = AsaasAPIError(
            "A API do Asaas retornou o status HTTP 500."
        )

        with self.assertRaisesMessage(AsaasAPIError, "HTTP 500"):
            sincronizar_responsavel_asaas(self.responsavel)

        self.responsavel.refresh_from_db()
        self.assertEqual(
            self.responsavel.asaas_customer_id,
            "cus_existente",
        )
        cliente.buscar_cliente_por_cpf_cnpj.assert_not_called()
        cliente.criar_cliente.assert_not_called()
