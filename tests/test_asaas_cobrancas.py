from datetime import date
from decimal import Decimal
from unittest.mock import call, patch

from django.test import TestCase

from academias.models import Academia
from atletas.models import Atleta, Responsavel
from financeiro.models import Mensalidade
from integracoes.asaas.client import AsaasAPIError
from integracoes.asaas.services import criar_cobranca_asaas
from matriculas.models import Matricula
from servicos.models import Servico


class CriarCobrancaAsaasTests(TestCase):
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
        )

    @patch("integracoes.asaas.services.AsaasClient")
    @patch("integracoes.asaas.services.sincronizar_responsavel_asaas")
    def test_cria_cobranca_pix_e_salva_id(
        self,
        mock_sincronizar,
        mock_client_class,
    ):
        mock_sincronizar.return_value = "cus_123"
        cliente = mock_client_class.return_value
        cliente.criar_cobranca.return_value = {"id": "pay_123"}

        payment_id = criar_cobranca_asaas(self.mensalidade)

        self.assertEqual(payment_id, "pay_123")
        self.mensalidade.refresh_from_db()
        self.assertEqual(self.mensalidade.asaas_payment_id, "pay_123")
        mock_sincronizar.assert_called_once_with(self.responsavel)
        cliente.criar_cobranca.assert_called_once_with(
            customer="cus_123",
            billing_type="PIX",
            valor=Decimal("135.50"),
            vencimento=date(2026, 9, 10),
            descricao="Mensalidade 09/2026",
        )

    @patch("integracoes.asaas.services.AsaasClient")
    @patch("integracoes.asaas.services.sincronizar_responsavel_asaas")
    def test_reutiliza_payment_id_sem_chamar_api(
        self,
        mock_sincronizar,
        mock_client_class,
    ):
        self.mensalidade.asaas_payment_id = "pay_existente"
        self.mensalidade.save(update_fields=["asaas_payment_id"])

        payment_id = criar_cobranca_asaas(self.mensalidade)

        self.assertEqual(payment_id, "pay_existente")
        mock_sincronizar.assert_not_called()
        mock_client_class.assert_not_called()

    @patch("integracoes.asaas.services.AsaasClient")
    def test_mensalidade_paga_nao_gera_cobranca(self, mock_client_class):
        self.mensalidade.status = "paga"
        self.mensalidade.save(update_fields=["status"])

        with self.assertRaisesMessage(
            ValueError,
            "Uma mensalidade paga não pode ser cobrada.",
        ):
            criar_cobranca_asaas(self.mensalidade)

        mock_client_class.assert_not_called()

    @patch("integracoes.asaas.services.AsaasClient")
    def test_mensalidade_cancelada_nao_gera_cobranca(
        self,
        mock_client_class,
    ):
        self.mensalidade.status = "cancelada"
        self.mensalidade.save(update_fields=["status"])

        with self.assertRaisesMessage(
            ValueError,
            "Uma mensalidade cancelada não pode ser cobrada.",
        ):
            criar_cobranca_asaas(self.mensalidade)

        mock_client_class.assert_not_called()

    @patch("integracoes.asaas.services.sincronizar_responsavel_asaas")
    def test_erro_quando_nao_existe_responsavel(self, mock_sincronizar):
        self.atleta.responsavel_financeiro = None
        self.atleta.save(update_fields=["responsavel_financeiro"])

        with self.assertRaisesMessage(
            ValueError,
            "O atleta não possui responsável financeiro",
        ):
            criar_cobranca_asaas(self.mensalidade)

        mock_sincronizar.assert_not_called()

    @patch("integracoes.asaas.services.AsaasClient")
    @patch("integracoes.asaas.services.sincronizar_responsavel_asaas")
    def test_propaga_erro_da_api_sem_salvar_payment_id(
        self,
        mock_sincronizar,
        mock_client_class,
    ):
        mock_sincronizar.return_value = "cus_123"
        cliente = mock_client_class.return_value
        cliente.criar_cobranca.side_effect = AsaasAPIError(
            "A API do Asaas retornou o status HTTP 500."
        )

        with self.assertRaisesMessage(AsaasAPIError, "HTTP 500"):
            criar_cobranca_asaas(self.mensalidade)

        self.mensalidade.refresh_from_db()
        self.assertEqual(self.mensalidade.asaas_payment_id, "")

    @patch("integracoes.asaas.services.AsaasClient")
    @patch("integracoes.asaas.services.sincronizar_responsavel_asaas")
    def test_erro_quando_resposta_nao_possui_id(
        self,
        mock_sincronizar,
        mock_client_class,
    ):
        mock_sincronizar.return_value = "cus_123"
        mock_client_class.return_value.criar_cobranca.return_value = {}

        with self.assertRaisesMessage(
            AsaasAPIError,
            "não contém o identificador da cobrança",
        ):
            criar_cobranca_asaas(self.mensalidade)

        self.mensalidade.refresh_from_db()
        self.assertEqual(self.mensalidade.asaas_payment_id, "")

    @patch("integracoes.asaas.services.AsaasClient")
    @patch("integracoes.asaas.services.sincronizar_responsavel_asaas")
    def test_chamadas_sequenciais_nao_criam_cobranca_duplicada(
        self,
        mock_sincronizar,
        mock_client_class,
    ):
        mock_sincronizar.return_value = "cus_123"
        cliente = mock_client_class.return_value
        cliente.criar_cobranca.return_value = {"id": "pay_unico"}

        resultados = [
            criar_cobranca_asaas(self.mensalidade),
            criar_cobranca_asaas(self.mensalidade),
        ]

        self.assertEqual(resultados, ["pay_unico", "pay_unico"])
        self.assertEqual(
            cliente.criar_cobranca.call_args_list,
            [
                call(
                    customer="cus_123",
                    billing_type="PIX",
                    valor=Decimal("135.50"),
                    vencimento=date(2026, 9, 10),
                    descricao="Mensalidade 09/2026",
                )
            ],
        )
        mock_sincronizar.assert_called_once_with(self.responsavel)
