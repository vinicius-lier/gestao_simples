"""FASE 1D — abstração de WhatsApp: factory + contrato neutro."""
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase

from academias.models import Academia, IntegracaoWhatsApp
from atletas.models import Atleta, Responsavel
from financeiro.models import Mensalidade
from integracoes.whatsapp import enviar_cobranca, get_provider
from integracoes.whatsapp.base import WhatsAppProvider, WhatsAppProviderError
from integracoes.whatsapp.evolution import EvolutionWhatsAppProvider
from integracoes.whatsapp.meta import MetaWhatsAppProvider
from matriculas.models import Matricula
from modalidades.models import Modalidade


class GetProviderTests(TestCase):
    def setUp(self):
        self.academia = Academia.objects.create(nome="Ac", cnpj="WP1")

    def test_sem_configuracao_usa_meta(self):
        provider = get_provider(self.academia)
        self.assertIsInstance(provider, MetaWhatsAppProvider)
        self.assertEqual(provider.nome, "meta")

    def test_configuracao_meta_usa_meta(self):
        IntegracaoWhatsApp.objects.create(
            academia=self.academia, provider=IntegracaoWhatsApp.PROVIDER_META
        )
        self.assertIsInstance(get_provider(self.academia), MetaWhatsAppProvider)

    def test_configuracao_evolution_usa_provider_evolution(self):
        IntegracaoWhatsApp.objects.create(
            academia=self.academia, provider=IntegracaoWhatsApp.PROVIDER_EVOLUTION
        )
        provider = get_provider(self.academia)
        self.assertIsInstance(provider, EvolutionWhatsAppProvider)
        self.assertIsInstance(provider, WhatsAppProvider)

    def test_evolution_sem_config_falha_de_forma_previsivel(self):
        provider = EvolutionWhatsAppProvider(config=None)
        responsavel = Responsavel.objects.create(
            academia=self.academia, nome="Ana", cpf="1", whatsapp="21999998888"
        )
        with self.assertRaisesMessage(WhatsAppProviderError, "não configurada"):
            provider.enviar_acesso(responsavel, "https://x")


class EnviarCobrancaNeutroTests(TestCase):
    def setUp(self):
        self.academia = Academia.objects.create(nome="Ac", cnpj="WP2")
        self.responsavel = Responsavel.objects.create(
            academia=self.academia, nome="Maria", cpf="1", whatsapp="21999999999"
        )
        self.atleta = Atleta.objects.create(
            academia=self.academia, nome="Filho", responsavel_financeiro=self.responsavel
        )
        self.modalidade = Modalidade.objects.create(academia=self.academia, nome="Judô")
        self.matricula = Matricula.objects.create(
            academia=self.academia, atleta=self.atleta, modalidade=self.modalidade,
            valor_mensalidade=Decimal("120.00"), dia_vencimento=10, data_inicio=date(2026, 1, 1),
        )
        self.mensalidade = Mensalidade.objects.create(
            academia=self.academia, matricula=self.matricula, competencia=date(2026, 9, 1),
            valor=Decimal("120.00"), vencimento=date(2026, 9, 10), status="pendente",
        )

    @patch("integracoes.whatsapp.services.WhatsAppClient")
    def test_neutro_delega_para_meta_e_normaliza_resultado(self, mock_client_class):
        mock_client_class.return_value.enviar_template.return_value = {
            "messages": [{"id": "wamid.ABC"}]
        }

        resultado = enviar_cobranca(
            self.academia, self.responsavel, self.mensalidade, "https://pgto/x"
        )

        self.assertEqual(resultado["provider"], "meta")
        self.assertEqual(resultado["message_id"], "wamid.ABC")
        mock_client_class.return_value.enviar_template.assert_called_once()

    @patch("integracoes.whatsapp.services.WhatsAppClient")
    def test_resposta_sem_wamid_devolve_message_id_vazio(self, mock_client_class):
        mock_client_class.return_value.enviar_template.return_value = {"ok": True}

        resultado = enviar_cobranca(
            self.academia, self.responsavel, self.mensalidade, "https://pgto/x"
        )

        self.assertEqual(resultado["message_id"], "")

    def test_academia_evolution_propaga_erro_previsivel(self):
        IntegracaoWhatsApp.objects.create(
            academia=self.academia, provider=IntegracaoWhatsApp.PROVIDER_EVOLUTION
        )
        with self.assertRaises(WhatsAppProviderError):
            enviar_cobranca(
                self.academia, self.responsavel, self.mensalidade, "https://pgto/x"
            )
