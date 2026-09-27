"""FASE 2A — EvolutionWhatsAppProvider real + garantia de que Meta segue igual."""
from datetime import date
from decimal import Decimal
from unittest.mock import Mock, patch

from django.test import TestCase, override_settings

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


@override_settings(EVOLUTION_BOTOES=True)
class EvolutionProviderTests(_Base):
    @patch("integracoes.evolution.client.client_para_config")
    def test_cobranca_vai_com_botao_que_abre_o_pagamento(self, mock_factory):
        cliente = Mock()
        cliente.enviar_botoes.return_value = {"key": {"id": "3EB0FF"}, "status": "PENDING"}
        mock_factory.return_value = cliente

        resultado = enviar_cobranca(
            self.academia, self.responsavel, self.mensalidade, "https://pgto/x"
        )

        self.assertEqual(resultado["provider"], "evolution")
        self.assertEqual(resultado["message_id"], "3EB0FF")
        cliente.enviar_texto.assert_not_called()
        numero, titulo, descricao, botoes = cliente.enviar_botoes.call_args.args
        self.assertEqual(numero, "5521999998888")  # normalizado
        self.assertEqual(titulo, "Mensalidade de Bruno")
        self.assertIn("Ana", descricao)
        # O link também vai no texto, para quem não enxerga o botão.
        self.assertIn("https://pgto/x", descricao)
        self.assertEqual(botoes, [{"type": "url", "displayText": "Pagar mensalidade", "url": "https://pgto/x"}])
        self.assertEqual(cliente.enviar_botoes.call_args.kwargs["rodape"], "Keiko")

    @patch("integracoes.evolution.client.client_para_config")
    def test_acesso_vai_com_botao_abrir_portal(self, mock_factory):
        cliente = Mock()
        cliente.enviar_botoes.return_value = {"key": {"id": "WAMID9"}}
        mock_factory.return_value = cliente

        resultado = EvolutionWhatsAppProvider(self.config).enviar_acesso(self.responsavel, "https://portal/entrar/abc")

        self.assertEqual(resultado["message_id"], "WAMID9")
        botoes = cliente.enviar_botoes.call_args.args[3]
        self.assertEqual(botoes, [{"type": "url", "displayText": "Abrir portal", "url": "https://portal/entrar/abc"}])
        self.assertIn("https://portal/entrar/abc", cliente.enviar_botoes.call_args.args[2])

    @patch("integracoes.evolution.client.client_para_config")
    def test_botao_recusado_pela_evolution_cai_para_texto(self, mock_factory):
        cliente = Mock()
        cliente.enviar_botoes.side_effect = EvolutionAPIError("A Evolution API retornou o status HTTP 400.")
        cliente.enviar_texto.return_value = {"key": {"id": "TXT1"}}
        mock_factory.return_value = cliente

        with self.assertLogs("integracoes.whatsapp.evolution", level="WARNING"):
            resultado = EvolutionWhatsAppProvider(self.config).enviar_cobranca(
                self.responsavel, self.mensalidade, "https://pgto/x"
            )

        self.assertEqual(resultado["message_id"], "TXT1")
        numero, texto = cliente.enviar_texto.call_args.args
        self.assertIn("Pague por aqui: https://pgto/x", texto)

    @override_settings(EVOLUTION_BOTOES=False)
    @patch("integracoes.evolution.client.client_para_config")
    def test_botoes_desligados_mandam_so_texto(self, mock_factory):
        cliente = Mock()
        cliente.enviar_texto.return_value = {"key": {"id": "TXT2"}}
        mock_factory.return_value = cliente

        EvolutionWhatsAppProvider(self.config).enviar_cobranca(self.responsavel, self.mensalidade, "https://x")

        cliente.enviar_botoes.assert_not_called()
        self.assertIn("https://x", cliente.enviar_texto.call_args.args[1])

    @patch("integracoes.evolution.client.client_para_config")
    def test_message_id_ausente_vira_string_vazia(self, mock_factory):
        cliente = Mock()
        cliente.enviar_botoes.return_value = {"status": "PENDING"}
        mock_factory.return_value = cliente

        resultado = EvolutionWhatsAppProvider(self.config).enviar_cobranca(
            self.responsavel, self.mensalidade, "https://x"
        )
        self.assertEqual(resultado["message_id"], "")

    @patch("integracoes.evolution.client.client_para_config")
    def test_erro_da_evolution_vira_whatsapp_provider_error(self, mock_factory):
        cliente = Mock()
        erro = EvolutionAPIError("A Evolution API retornou o status HTTP 500.")
        cliente.enviar_botoes.side_effect = erro
        cliente.enviar_texto.side_effect = erro
        mock_factory.return_value = cliente

        with self.assertLogs("integracoes.whatsapp.evolution", level="WARNING"), \
                self.assertRaises(WhatsAppProviderError):
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
