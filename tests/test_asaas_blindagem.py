"""FASE 1A — blindagem da criação de cobrança no Asaas.

Cobre: lock + recheck de asaas_payment_id, externalReference no payload,
reconciliação por externalReference em caso de timeout, e a garantia de que
um erro não apaga dados já persistidos.
"""
from datetime import date
from decimal import Decimal
from unittest.mock import Mock, patch

from django.db import connection
from django.test import SimpleTestCase, TestCase, TransactionTestCase

from academias.models import Academia
from atletas.models import Atleta, Responsavel
from financeiro.models import Mensalidade
from integracoes.asaas.client import AsaasAPIError, AsaasClient
from integracoes.asaas.services import (
    criar_cobranca_asaas,
    criar_cobranca_multipla_asaas,
    external_reference_da_mensalidade,
    garantir_cobranca_asaas,
)
from matriculas.models import Matricula
from modalidades.models import Modalidade


def _cria_mensalidade():
    academia = Academia.objects.create(nome="Ac", cnpj=f"CN{Academia.objects.count()}")
    responsavel = Responsavel.objects.create(
        academia=academia, nome="Resp", cpf="12345678901", whatsapp="5521999999999"
    )
    atleta = Atleta.objects.create(
        academia=academia, nome="Aluno", responsavel_financeiro=responsavel
    )
    modalidade = Modalidade.objects.create(academia=academia, nome="Judô")
    matricula = Matricula.objects.create(
        academia=academia, atleta=atleta, modalidade=modalidade,
        valor_mensalidade=Decimal("120.00"), dia_vencimento=10, data_inicio=date(2026, 1, 1),
    )
    return Mensalidade.objects.create(
        academia=academia, matricula=matricula, competencia=date(2026, 9, 1),
        valor=Decimal("120.00"), vencimento=date(2026, 9, 10), status="pendente",
    )


class ExternalReferenceClientTests(SimpleTestCase):
    def setUp(self):
        env = {"ASAAS_BASE_URL": "https://api.example.com/v3", "ASAAS_API_KEY": "k"}
        with patch.dict("os.environ", env):
            self.client = AsaasClient()

    @patch("integracoes.asaas.client.requests.post")
    def test_external_reference_vai_no_payload_quando_informado(self, mock_post):
        resp = Mock(status_code=200)
        resp.json.return_value = {"id": "pay_1"}
        mock_post.return_value = resp

        self.client.criar_cobranca(
            customer="cus_1", valor=Decimal("10.00"), vencimento=date(2026, 9, 10),
            descricao="x", billing_type="UNDEFINED", external_reference="mensalidade:7",
        )

        self.assertEqual(
            mock_post.call_args.kwargs["json"]["externalReference"], "mensalidade:7"
        )

    @patch("integracoes.asaas.client.requests.post")
    def test_sem_external_reference_a_chave_nao_aparece(self, mock_post):
        resp = Mock(status_code=200)
        resp.json.return_value = {"id": "pay_1"}
        mock_post.return_value = resp

        self.client.criar_cobranca(
            customer="cus_1", valor=Decimal("10.00"), vencimento=date(2026, 9, 10),
            descricao="x",
        )

        self.assertNotIn("externalReference", mock_post.call_args.kwargs["json"])

    @patch("integracoes.asaas.client.requests.get")
    def test_buscar_cobranca_por_external_reference(self, mock_get):
        resp = Mock(status_code=200)
        resp.json.return_value = {"data": [{"id": "pay_ref", "invoiceUrl": "u"}]}
        mock_get.return_value = resp

        achado = self.client.buscar_cobranca_por_external_reference("mensalidade:7")

        self.assertEqual(achado["id"], "pay_ref")
        self.assertEqual(
            mock_get.call_args.kwargs["params"], {"externalReference": "mensalidade:7"}
        )

    @patch("integracoes.asaas.client.requests.get")
    def test_buscar_cobranca_sem_resultado_retorna_none(self, mock_get):
        resp = Mock(status_code=200)
        resp.json.return_value = {"data": []}
        mock_get.return_value = resp

        self.assertIsNone(
            self.client.buscar_cobranca_por_external_reference("mensalidade:9")
        )


class CriarCobrancaBlindadaTests(TestCase):
    def setUp(self):
        self.mensalidade = _cria_mensalidade()
        self.responsavel = self.mensalidade.matricula.atleta.responsavel_financeiro

    @patch("integracoes.asaas.services.AsaasClient")
    @patch("integracoes.asaas.services.sincronizar_responsavel_asaas")
    def test_envia_external_reference_estavel(self, mock_sinc, mock_client_class):
        mock_sinc.return_value = "cus_1"
        mock_client_class.return_value.criar_cobranca.return_value = {"id": "pay_1"}

        criar_cobranca_multipla_asaas(self.mensalidade)

        kwargs = mock_client_class.return_value.criar_cobranca.call_args.kwargs
        self.assertEqual(kwargs["billing_type"], "UNDEFINED")
        self.assertEqual(
            kwargs["external_reference"], f"mensalidade:{self.mensalidade.pk}"
        )
        self.assertNotIn("12345678901", str(kwargs))  # sem CPF/dado pessoal

    @patch("integracoes.asaas.services.AsaasClient")
    @patch("integracoes.asaas.services.sincronizar_responsavel_asaas")
    def test_duas_chamadas_sequenciais_criam_uma_cobranca(self, mock_sinc, mock_client_class):
        mock_sinc.return_value = "cus_1"
        mock_client_class.return_value.criar_cobranca.return_value = {"id": "pay_1"}

        a = criar_cobranca_multipla_asaas(self.mensalidade)
        b = criar_cobranca_multipla_asaas(self.mensalidade)

        self.assertEqual((a, b), ("pay_1", "pay_1"))
        mock_client_class.return_value.criar_cobranca.assert_called_once()

    @patch("integracoes.asaas.services.AsaasClient")
    @patch("integracoes.asaas.services.sincronizar_responsavel_asaas")
    def test_recheck_sob_lock_adota_cobranca_criada_por_outra_transacao(
        self, mock_sinc, mock_client_class
    ):
        # instância "velha" em memória (sem payment_id)...
        stale = Mensalidade.objects.get(pk=self.mensalidade.pk)
        # ...enquanto outra "requisição" persiste a cobrança direto no banco.
        Mensalidade.objects.filter(pk=self.mensalidade.pk).update(
            asaas_payment_id="pay_race", asaas_invoice_url="https://asaas/i/race"
        )

        payment_id = criar_cobranca_multipla_asaas(stale)

        self.assertEqual(payment_id, "pay_race")
        self.assertEqual(stale.asaas_payment_id, "pay_race")
        self.assertEqual(stale.asaas_invoice_url, "https://asaas/i/race")
        mock_sinc.assert_not_called()
        mock_client_class.assert_not_called()

    @patch("integracoes.asaas.services.AsaasClient")
    @patch("integracoes.asaas.services.sincronizar_responsavel_asaas")
    def test_timeout_na_criacao_reconcilia_e_nao_cria_segunda(
        self, mock_sinc, mock_client_class
    ):
        mock_sinc.return_value = "cus_1"
        cliente = mock_client_class.return_value
        cliente.criar_cobranca.side_effect = AsaasAPIError(
            "Não foi possível comunicar com a API do Asaas."
        )
        cliente.buscar_cobranca_por_external_reference.return_value = {
            "id": "pay_remoto", "invoiceUrl": "https://asaas/i/remoto",
        }

        payment_id = criar_cobranca_multipla_asaas(self.mensalidade)

        self.assertEqual(payment_id, "pay_remoto")
        self.mensalidade.refresh_from_db()
        self.assertEqual(self.mensalidade.asaas_payment_id, "pay_remoto")
        self.assertEqual(self.mensalidade.asaas_invoice_url, "https://asaas/i/remoto")
        cliente.buscar_cobranca_por_external_reference.assert_called_once_with(
            f"mensalidade:{self.mensalidade.pk}"
        )

    @patch("integracoes.asaas.services.AsaasClient")
    @patch("integracoes.asaas.services.sincronizar_responsavel_asaas")
    def test_timeout_sem_reconciliacao_propaga_erro_sem_apagar_dados(
        self, mock_sinc, mock_client_class
    ):
        self.mensalidade.asaas_invoice_url = "https://asaas/i/antigo"
        self.mensalidade.save(update_fields=["asaas_invoice_url"])
        mock_sinc.return_value = "cus_1"
        cliente = mock_client_class.return_value
        cliente.criar_cobranca.side_effect = AsaasAPIError("timeout")
        cliente.buscar_cobranca_por_external_reference.return_value = None

        with self.assertRaises(AsaasAPIError):
            criar_cobranca_multipla_asaas(self.mensalidade)

        self.mensalidade.refresh_from_db()
        self.assertEqual(self.mensalidade.asaas_payment_id, "")
        self.assertEqual(self.mensalidade.asaas_invoice_url, "https://asaas/i/antigo")

    @patch("integracoes.asaas.services.AsaasClient")
    @patch("integracoes.asaas.services.sincronizar_responsavel_asaas")
    def test_mensalidade_com_payment_id_nao_chama_api(self, mock_sinc, mock_client_class):
        self.mensalidade.asaas_payment_id = "pay_existente"
        self.mensalidade.save(update_fields=["asaas_payment_id"])

        self.assertEqual(
            criar_cobranca_multipla_asaas(self.mensalidade), "pay_existente"
        )
        mock_sinc.assert_not_called()
        mock_client_class.assert_not_called()

    @patch("integracoes.asaas.services.AsaasClient")
    @patch("integracoes.asaas.services.sincronizar_responsavel_asaas")
    def test_garantir_cobranca_reutiliza_existente(self, mock_sinc, mock_client_class):
        self.mensalidade.asaas_payment_id = "pay_existente"
        self.mensalidade.save(update_fields=["asaas_payment_id"])

        self.assertEqual(garantir_cobranca_asaas(self.mensalidade), "pay_existente")
        mock_client_class.assert_not_called()

    def test_external_reference_helper_formato(self):
        self.assertEqual(
            external_reference_da_mensalidade(self.mensalidade),
            f"mensalidade:{self.mensalidade.pk}",
        )


class CriarCobrancaConcorrenteTests(TransactionTestCase):
    """Concorrência real — só roda no PostgreSQL, onde select_for_update()
    efetivamente trava a linha. No SQLite o teste acima
    (test_recheck_sob_lock_adota_cobranca_criada_por_outra_transacao) já
    cobre a lógica de recheck."""

    available_apps = None

    def setUp(self):
        self.mensalidade = _cria_mensalidade()

    def test_duas_threads_criam_uma_unica_cobranca(self):
        if connection.vendor != "postgresql":
            self.skipTest("select_for_update só trava de fato no PostgreSQL")

        import threading

        criadas = []

        def _worker():
            with patch("integracoes.asaas.services.sincronizar_responsavel_asaas", return_value="cus_1"), \
                 patch("integracoes.asaas.services.AsaasClient") as mock_client_class:
                mock_client_class.return_value.criar_cobranca.return_value = {
                    "id": f"pay_{threading.get_ident()}"
                }
                try:
                    criadas.append(criar_cobranca_multipla_asaas(
                        Mensalidade.objects.get(pk=self.mensalidade.pk)
                    ))
                finally:
                    connection.close()

        ts = [threading.Thread(target=_worker) for _ in range(2)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()

        self.mensalidade.refresh_from_db()
        self.assertEqual(len(set(criadas)), 1)
        self.assertEqual(criadas[0], self.mensalidade.asaas_payment_id)
