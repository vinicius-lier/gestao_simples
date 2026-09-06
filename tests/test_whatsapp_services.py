from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase

from academias.models import Academia
from atletas.models import Atleta, Responsavel
from integracoes.whatsapp.services import (
    enviar_acesso_portal_responsavel,
    enviar_cobranca_responsavel,
    normalizar_telefone,
)
from matriculas.models import Matricula
from modalidades.models import Modalidade


class NormalizarTelefoneTests(SimpleTestCase):
    def test_adiciona_ddi_55_em_numero_de_11_digitos(self):
        self.assertEqual(normalizar_telefone("(21) 99999-9999"), "5521999999999")

    def test_adiciona_ddi_55_em_numero_de_10_digitos(self):
        self.assertEqual(normalizar_telefone("21 3333-4444"), "552133334444")

    def test_nao_duplica_ddi_ja_presente(self):
        self.assertEqual(normalizar_telefone("5521999999999"), "5521999999999")

    def test_vazio_continua_vazio(self):
        self.assertEqual(normalizar_telefone(""), "")
        self.assertEqual(normalizar_telefone(None), "")


class EnviarAcessoPortalResponsavelTests(TestCase):
    def setUp(self):
        self.academia = Academia.objects.create(nome="Academia Teste", cnpj="WA1")
        self.responsavel = Responsavel.objects.create(
            academia=self.academia, nome="Maria", cpf="1", whatsapp="21999999999"
        )

    @patch("integracoes.whatsapp.services.WhatsAppClient")
    def test_envia_com_nome_e_link(self, mock_client_class):
        mock_client_class.return_value.enviar_template.return_value = {"ok": True}

        enviar_acesso_portal_responsavel(self.responsavel, "https://portal/entrar/abc")

        mock_client_class.return_value.enviar_template.assert_called_once_with(
            "5521999999999",
            nome_template="acesso_portal",
            parametros=["Maria", "https://portal/entrar/abc"],
        )

    @patch.dict("os.environ", {"WHATSAPP_TEMPLATE_ACESSO": "meu_template"})
    @patch("integracoes.whatsapp.services.WhatsAppClient")
    def test_usa_template_configurado(self, mock_client_class):
        enviar_acesso_portal_responsavel(self.responsavel, "https://x")
        kwargs = mock_client_class.return_value.enviar_template.call_args.kwargs
        self.assertEqual(kwargs["nome_template"], "meu_template")

    def test_erro_sem_whatsapp_cadastrado(self):
        self.responsavel.whatsapp = ""
        self.responsavel.save(update_fields=["whatsapp"])
        with self.assertRaisesMessage(ValueError, "não possui WhatsApp"):
            enviar_acesso_portal_responsavel(self.responsavel, "https://x")


class EnviarCobrancaResponsavelTests(TestCase):
    def setUp(self):
        self.academia = Academia.objects.create(nome="Academia Teste", cnpj="WA2")
        self.responsavel = Responsavel.objects.create(
            academia=self.academia, nome="João", cpf="1", whatsapp="21988887777"
        )
        self.atleta = Atleta.objects.create(
            academia=self.academia, nome="Filho", responsavel_financeiro=self.responsavel
        )
        self.modalidade = Modalidade.objects.create(academia=self.academia, nome="Judô")
        self.matricula = Matricula.objects.create(
            academia=self.academia, atleta=self.atleta, modalidade=self.modalidade,
            valor_mensalidade=Decimal("120.00"), dia_vencimento=10, data_inicio=date(2026, 1, 1),
        )

    @patch("integracoes.whatsapp.services.WhatsAppClient")
    def test_envia_parametros_da_mensalidade(self, mock_client_class):
        from financeiro.models import Mensalidade

        mensalidade = Mensalidade.objects.create(
            academia=self.academia, matricula=self.matricula, competencia=date(2026, 9, 1),
            valor=Decimal("120.00"), vencimento=date(2026, 9, 10), status="pendente",
        )

        enviar_cobranca_responsavel(self.responsavel, mensalidade, "https://portal/pagar/1")

        mock_client_class.return_value.enviar_template.assert_called_once_with(
            "5521988887777",
            nome_template="cobranca_mensalidade",
            parametros=["João", "09/2026", "R$ 120.00", "10/09/2026", "https://portal/pagar/1"],
        )
