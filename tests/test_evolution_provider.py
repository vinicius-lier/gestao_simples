"""FASE 2A — EvolutionWhatsAppProvider real + garantia de que Meta segue igual."""
from datetime import date
from decimal import Decimal
from unittest.mock import Mock, patch

from django.test import TestCase

from academias.models import Academia, IntegracaoWhatsApp
from atletas.models import Atleta, Responsavel
from financeiro.models import Mensalidade
from integracoes.evolution.client import EvolutionAPIError
from integracoes.whatsapp import enviar_cobranca
from integracoes.whatsapp.base import WhatsAppProviderError
from integracoes.whatsapp.evolution import EvolutionWhatsAppProvider
from matriculas.models import Matricula
from modalidades.models import Modalidade


class _Base(TestCase):
    def setUp(self):
        self.academia = Academia.objects.create(nome="Keiko", cnpj="EVP1")
        self.responsavel = Responsavel.objects.create(
            academia=self.academia, nome="Ana", cpf="1", whatsapp="(21) 99999-8888"
        )
        self.atleta = Atleta.objects.create(
            academia=self.academia, nome="Bruno", responsavel_financeiro=self.responsavel
        )
        self.modalidade = Modalidade.objects.create(academia=self.academia, nome="Judô")
        self.matricula = Matricula.objects.create(
            academia=self.academia, atleta=self.atleta, modalidade=self.modalidade,
            valor_mensalidade=Decimal("150.00"), dia_vencimento=10, data_inicio=date(2026, 1, 1),
        )
        self.mensalidade = Mensalidade.objects.create(
            academia=self.academia, matricula=self.matricula, competencia=date(2026, 9, 1),
            valor=Decimal("150.00"), vencimento=date(2026, 9, 10), status="pendente",
        )
        self.config = IntegracaoWhatsApp.objects.create(
            academia=self.academia,
            provider=IntegracaoWhatsApp.PROVIDER_EVOLUTION,
            evolution_base_url="https://evo.example.com",
            evolution_instance_name="keiko",
            credencial_ref="EVOLUTION_API_KEY_TESTE",
        )


class EvolutionProviderTests(_Base):
    @patch("integracoes.evolution.client.client_para_config")
    def test_enviar_cobranca_normaliza_retorno(self, mock_factory):
        cliente = Mock()
        cliente.enviar_texto.return_value = {"key": {"id": "3EB0FF"}, "status": "PENDING"}
        mock_factory.return_value = cliente

        resultado = enviar_cobranca(
            self.academia, self.responsavel, self.mensalidade, "https://pgto/x"
        )

        self.assertEqual(resultado["provider"], "evolution")
        self.assertEqual(resultado["message_id"], "3EB0FF")
        numero, texto = cliente.enviar_texto.call_args.args
        self.assertEqual(numero, "5521999998888")  # normalizado
        self.assertIn("https://pgto/x", texto)
        self.assertIn("Bruno", texto)

    @patch("integracoes.evolution.client.client_para_config")
    def test_enviar_acesso(self, mock_factory):
        cliente = Mock()
        cliente.enviar_texto.return_value = {"key": {"id": "WAMID9"}}
        mock_factory.return_value = cliente

        provider = EvolutionWhatsAppProvider(self.config)
        resultado = provider.enviar_acesso(self.responsavel, "https://portal/entrar/abc")

        self.assertEqual(resultado["message_id"], "WAMID9")
        self.assertIn("https://portal/entrar/abc", cliente.enviar_texto.call_args.args[1])

    @patch("integracoes.evolution.client.client_para_config")
    def test_message_id_ausente_vira_string_vazia(self, mock_factory):
        cliente = Mock()
        cliente.enviar_texto.return_value = {"status": "PENDING"}
        mock_factory.return_value = cliente

        resultado = EvolutionWhatsAppProvider(self.config).enviar_cobranca(
            self.responsavel, self.mensalidade, "https://x"
        )
        self.assertEqual(resultado["message_id"], "")

    @patch("integracoes.evolution.client.client_para_config")
    def test_erro_da_evolution_vira_whatsapp_provider_error(self, mock_factory):
        cliente = Mock()
        cliente.enviar_texto.side_effect = EvolutionAPIError(
            "A Evolution API retornou o status HTTP 500."
        )
        mock_factory.return_value = cliente

        with self.assertRaises(WhatsAppProviderError):
            EvolutionWhatsAppProvider(self.config).enviar_cobranca(
                self.responsavel, self.mensalidade, "https://x"
            )

    def test_config_none(self):
        with self.assertRaisesMessage(WhatsAppProviderError, "não configurada"):
            EvolutionWhatsAppProvider(None).enviar_acesso(self.responsavel, "https://x")

    def test_responsavel_sem_whatsapp(self):
        self.responsavel.whatsapp = ""
        with self.assertRaisesMessage(WhatsAppProviderError, "WhatsApp"):
            EvolutionWhatsAppProvider(self.config).enviar_acesso(self.responsavel, "https://x")


class MetaContinuaFuncionandoTests(_Base):
    @patch("integracoes.whatsapp.services.WhatsAppClient")
    def test_academia_meta_ainda_envia_via_meta(self, mock_client_class):
        self.config.provider = IntegracaoWhatsApp.PROVIDER_META
        self.config.save(update_fields=["provider"])
        mock_client_class.return_value.enviar_template.return_value = {
            "messages": [{"id": "wamid.META"}]
        }

        resultado = enviar_cobranca(
            self.academia, self.responsavel, self.mensalidade, "https://x"
        )

        self.assertEqual(resultado["provider"], "meta")
        self.assertEqual(resultado["message_id"], "wamid.META")
        mock_client_class.return_value.enviar_template.assert_called_once()

    @patch("integracoes.whatsapp.services.WhatsAppClient")
    def test_academia_sem_config_usa_meta(self, mock_client_class):
        self.config.delete()
        mock_client_class.return_value.enviar_template.return_value = {"ok": True}

        resultado = enviar_cobranca(
            self.academia, self.responsavel, self.mensalidade, "https://x"
        )
        self.assertEqual(resultado["provider"], "meta")
