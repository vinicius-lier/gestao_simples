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


@patch("integracoes.evolution.services.client_para_config")
class ConectarTests(TestCase):
    """Número -> QR Code: o único passo da tela de WhatsApp da academia."""

    def setUp(self):
        self.academia = Academia.objects.create(nome="Nova", cnpj="EVS9")

    def cliente(self, estado=None, **retornos):
        cliente = Mock()
        if estado == 404:
            cliente.buscar_estado_conexao.side_effect = EvolutionAPIError("HTTP 404", 404)
        else:
            cliente.buscar_estado_conexao.return_value = {"instance": {"state": estado or "close"}}
        for metodo, valor in retornos.items():
            getattr(cliente, metodo).return_value = valor
        return cliente

    def test_primeira_vez_cria_a_instancia_e_devolve_o_qr(self, factory):
        factory.return_value = cliente = self.cliente(404, criar_instancia={"qrcode": {"base64": "QQ"}})

        qr = evo.conectar(self.academia, "(21) 99999-8888")

        cliente.criar_instancia.assert_called_once_with(numero="5521999998888")
        self.assertEqual(qr["base64"], "data:image/png;base64,QQ")
        config = IntegracaoWhatsApp.objects.get(academia=self.academia)
        self.assertEqual(config.provider, IntegracaoWhatsApp.PROVIDER_EVOLUTION)
        self.assertEqual(config.evolution_instance_name, f"academia-{self.academia.pk}")
        self.assertEqual(config.numero_whatsapp, "5521999998888")
        self.assertEqual(config.status_conexao, IntegracaoWhatsApp.STATUS_AGUARDANDO_QRCODE)

    def test_instancia_existente_desconectada_gera_qr(self, factory):
        factory.return_value = cliente = self.cliente("close", obter_qrcode={"base64": "ZZ"})
        qr = evo.conectar(self.academia, "21999998888")
        cliente.criar_instancia.assert_not_called()
        self.assertEqual(qr["base64"], "data:image/png;base64,ZZ")

    def test_ja_conectado_com_o_mesmo_numero_nao_gera_qr(self, factory):
        IntegracaoWhatsApp.objects.create(academia=self.academia, numero_whatsapp="5521999998888")
        factory.return_value = cliente = self.cliente("open")
        self.assertTrue(evo.conectar(self.academia, "21999998888")["conectado"])
        cliente.obter_qrcode.assert_not_called()
        cliente.logout.assert_not_called()

    def test_numero_novo_derruba_a_sessao_antiga(self, factory):
        IntegracaoWhatsApp.objects.create(
            academia=self.academia, numero_whatsapp="5521911112222", evolution_instance_name="keiko",
        )
        factory.return_value = cliente = self.cliente("open", obter_qrcode={"base64": "NN"})
        evo.conectar(self.academia, "21999998888")
        cliente.logout.assert_called_once()
        config = IntegracaoWhatsApp.objects.get(academia=self.academia)
        # Instância já existente é mantida; só o número muda.
        self.assertEqual((config.evolution_instance_name, config.numero_whatsapp), ("keiko", "5521999998888"))

    def test_numero_sem_ddd_e_recusado(self, factory):
        with self.assertRaisesMessage(EvolutionConfigError, "com DDD"):
            evo.conectar(self.academia, "99998888")
        factory.assert_not_called()

    def test_falha_da_evolution_marca_erro(self, factory):
        cliente = self.cliente()
        cliente.buscar_estado_conexao.side_effect = EvolutionAPIError("HTTP 500", 500)
        factory.return_value = cliente
        with self.assertRaises(EvolutionAPIError):
            evo.conectar(self.academia, "21999998888")
        config = IntegracaoWhatsApp.objects.get(academia=self.academia)
        self.assertEqual(config.status_conexao, IntegracaoWhatsApp.STATUS_ERRO)
