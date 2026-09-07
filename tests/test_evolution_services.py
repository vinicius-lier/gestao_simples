"""FASE 2A — serviços de gestão da instância Evolution."""
from unittest.mock import Mock, patch

from django.test import TestCase

from academias.models import Academia, IntegracaoWhatsApp
from integracoes.evolution import services as evo
from integracoes.evolution.client import EvolutionAPIError, EvolutionConfigError


class _Base(TestCase):
    def setUp(self):
        self.academia = Academia.objects.create(nome="Keiko", cnpj="EVS1")
        self.config = IntegracaoWhatsApp.objects.create(
            academia=self.academia,
            provider=IntegracaoWhatsApp.PROVIDER_EVOLUTION,
            evolution_base_url="https://evo.example.com",
            evolution_instance_name="keiko",
            credencial_ref="EVOLUTION_API_KEY_TESTE",
        )

    def _mock_client(self, **retornos):
        cliente = Mock()
        for metodo, valor in retornos.items():
            getattr(cliente, metodo).return_value = valor
        return cliente


class ParsingHelpersTests(TestCase):
    def test_mapear_status(self):
        self.assertEqual(evo.mapear_status("open"), IntegracaoWhatsApp.STATUS_CONECTADO)
        self.assertEqual(evo.mapear_status("connecting"), IntegracaoWhatsApp.STATUS_AGUARDANDO_QRCODE)
        self.assertEqual(evo.mapear_status("close"), IntegracaoWhatsApp.STATUS_DESCONECTADO)
        self.assertEqual(evo.mapear_status("banana"), IntegracaoWhatsApp.STATUS_ERRO)
        self.assertEqual(evo.mapear_status(""), IntegracaoWhatsApp.STATUS_ERRO)

    def test_extrair_estado_varias_formas(self):
        self.assertEqual(evo.extrair_estado({"instance": {"state": "open"}}), "open")
        self.assertEqual(evo.extrair_estado({"state": "close"}), "close")
        self.assertEqual(evo.extrair_estado({"status": "connecting"}), "connecting")
        self.assertEqual(evo.extrair_estado({"nada": 1}), "")

    def test_extrair_numero_remove_sufixo_jid(self):
        self.assertEqual(
            evo.extrair_numero({"instance": {"owner": "5521999998888@s.whatsapp.net"}}),
            "5521999998888",
        )
        self.assertEqual(evo.extrair_numero({"number": "5521999998888"}), "5521999998888")
        self.assertEqual(evo.extrair_numero({}), "")

    def test_normalizar_qrcode(self):
        self.assertEqual(
            evo.normalizar_qrcode({"base64": "data:image/png;base64,AAA", "code": "1@x"}),
            {"base64": "data:image/png;base64,AAA", "code": "1@x"},
        )
        # base64 puro ganha o prefixo data:
        self.assertEqual(
            evo.normalizar_qrcode({"base64": "AAA"})["base64"],
            "data:image/png;base64,AAA",
        )
        # forma aninhada + pairingCode
        norm = evo.normalizar_qrcode({"qrcode": {"base64": "BBB", "pairingCode": "ABCD"}})
        self.assertEqual(norm["base64"], "data:image/png;base64,BBB")
        self.assertEqual(norm["code"], "ABCD")
        self.assertEqual(evo.normalizar_qrcode("x"), {"base64": None, "code": None})


class CriarInstanciaTests(_Base):
    @patch("integracoes.evolution.services.client_para_config")
    def test_cria_e_marca_aguardando_qrcode(self, mock_factory):
        mock_factory.return_value = self._mock_client(
            criar_instancia={"instance": {"instanceName": "keiko"}, "qrcode": {"base64": "AAA"}}
        )

        resultado = evo.criar_instancia(self.academia)

        self.config.refresh_from_db()
        self.assertEqual(self.config.status_conexao, IntegracaoWhatsApp.STATUS_AGUARDANDO_QRCODE)
        self.assertIsNotNone(self.config.ultimo_status_em)
        self.assertEqual(resultado["qrcode"]["base64"], "data:image/png;base64,AAA")

    @patch("integracoes.evolution.services.client_para_config")
    def test_erro_marca_status_erro_e_propaga(self, mock_factory):
        cliente = self._mock_client()
        cliente.criar_instancia.side_effect = EvolutionAPIError("HTTP 500")
        mock_factory.return_value = cliente

        with self.assertRaises(EvolutionAPIError):
            evo.criar_instancia(self.academia)

        self.config.refresh_from_db()
        self.assertEqual(self.config.status_conexao, IntegracaoWhatsApp.STATUS_ERRO)


class ConsultarStatusTests(_Base):
    @patch("integracoes.evolution.services.client_para_config")
    def test_conectado_atualiza_numero_e_ultima_conexao(self, mock_factory):
        mock_factory.return_value = self._mock_client(
            buscar_estado_conexao={
                "instance": {"state": "open", "owner": "5521988887777@s.whatsapp.net"}
            }
        )

        resultado = evo.consultar_status(self.academia)

        self.config.refresh_from_db()
        self.assertEqual(resultado["status"], IntegracaoWhatsApp.STATUS_CONECTADO)
        self.assertEqual(self.config.status_conexao, IntegracaoWhatsApp.STATUS_CONECTADO)
        self.assertEqual(self.config.numero_whatsapp, "5521988887777")
        self.assertIsNotNone(self.config.ultima_conexao_em)

    @patch("integracoes.evolution.services.client_para_config")
    def test_desconectado(self, mock_factory):
        mock_factory.return_value = self._mock_client(
            buscar_estado_conexao={"instance": {"state": "close"}}
        )
        evo.consultar_status(self.academia)
        self.config.refresh_from_db()
        self.assertEqual(self.config.status_conexao, IntegracaoWhatsApp.STATUS_DESCONECTADO)

    @patch("integracoes.evolution.services.client_para_config")
    def test_estado_desconhecido_vira_erro(self, mock_factory):
        mock_factory.return_value = self._mock_client(
            buscar_estado_conexao={"instance": {"state": "weird"}}
        )
        evo.consultar_status(self.academia)
        self.config.refresh_from_db()
        self.assertEqual(self.config.status_conexao, IntegracaoWhatsApp.STATUS_ERRO)
        self.assertEqual(self.config.ultimo_status, "weird")


class GerarQrcodeTests(_Base):
    @patch("integracoes.evolution.services.client_para_config")
    def test_retorna_qr_e_nao_persiste_base64(self, mock_factory):
        mock_factory.return_value = self._mock_client(
            obter_qrcode={"base64": "QQQ", "code": "2@abc"}
        )

        qr = evo.gerar_qrcode(self.academia)

        self.assertEqual(qr, {"base64": "data:image/png;base64,QQQ", "code": "2@abc"})
        self.config.refresh_from_db()
        self.assertEqual(
            self.config.status_conexao, IntegracaoWhatsApp.STATUS_AGUARDANDO_QRCODE
        )
        # o base64 não pode ter sido gravado em nenhum campo de texto
        for campo in ("ultimo_status", "numero_whatsapp", "evolution_instance_name"):
            self.assertNotIn("QQQ", getattr(self.config, campo))

    @patch("integracoes.evolution.services.client_para_config")
    def test_qr_ja_conectado_reflete_status(self, mock_factory):
        mock_factory.return_value = self._mock_client(
            obter_qrcode={"instance": {"state": "open"}}
        )
        evo.gerar_qrcode(self.academia)
        self.config.refresh_from_db()
        self.assertEqual(self.config.status_conexao, IntegracaoWhatsApp.STATUS_CONECTADO)


class DesconectarTrocarNumeroTests(_Base):
    @patch("integracoes.evolution.services.client_para_config")
    def test_desconectar(self, mock_factory):
        cliente = self._mock_client(logout={"status": "SUCCESS"})
        mock_factory.return_value = cliente

        evo.desconectar(self.academia)

        cliente.logout.assert_called_once()
        self.config.refresh_from_db()
        self.assertEqual(self.config.status_conexao, IntegracaoWhatsApp.STATUS_DESCONECTADO)

    @patch("integracoes.evolution.services.client_para_config")
    def test_trocar_numero_normaliza_desloga_e_salva(self, mock_factory):
        cliente = self._mock_client(logout={})
        mock_factory.return_value = cliente

        resultado = evo.trocar_numero(self.academia, "(21) 98888-7777")

        cliente.logout.assert_called_once()
        self.config.refresh_from_db()
        self.assertEqual(self.config.numero_whatsapp, "5521988887777")
        self.assertEqual(self.config.status_conexao, IntegracaoWhatsApp.STATUS_DESCONECTADO)
        self.assertEqual(resultado["numero"], "5521988887777")

    @patch("integracoes.evolution.services.client_para_config")
    def test_trocar_numero_invalido(self, mock_factory):
        with self.assertRaises(EvolutionConfigError):
            evo.trocar_numero(self.academia, "abc")
        mock_factory.assert_not_called()


class ConfigGuardTests(_Base):
    def test_academia_sem_config(self):
        outra = Academia.objects.create(nome="Outra", cnpj="EVS2")
        with self.assertRaises(EvolutionConfigError):
            evo.consultar_status(outra)

    def test_academia_com_provider_meta(self):
        self.config.provider = IntegracaoWhatsApp.PROVIDER_META
        self.config.save(update_fields=["provider"])
        with self.assertRaises(EvolutionConfigError):
            evo.consultar_status(self.academia)
