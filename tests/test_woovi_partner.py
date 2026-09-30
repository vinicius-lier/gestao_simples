"""Conexão da conta própria pela Partner API da Woovi (sem rede real)."""
import base64
from io import StringIO
from unittest.mock import Mock, patch

from cryptography.fernet import Fernet
from django.contrib import admin
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from academias.models import Academia
from financeiro.models import ContaRecebimento
from integracoes.woovi import partner
from integracoes.woovi.client import WooviClient
from integracoes.woovi.contas import conectar_automaticamente, conectar_conta, preparar_conta
from integracoes.woovi.credenciais import da_conta, fingerprint, referencia_academia
from integracoes.woovi.cripto import cifrar, decifrar
from integracoes.woovi.exceptions import WooviAuthError, WooviConfigError, WooviInvalidResponseError
from portal.models import AcessoAcademia
from tests.woovi_base import CenarioWoovi

CHAVE_FERNET = Fernet.generate_key().decode()
PARCEIRO = "segredo-da-conta-parceira"
CLIENT_ID = "client-id-da-afiliada"
CLIENT_SECRET = "client-secret-da-afiliada-nao-pode-vazar"
APP_ID = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
CNPJ = "12345678000195"
KYC = "https://kyc.woovi.com/onboarding/cadastro-1"


def http(dados, status=200):
    return Mock(status_code=status, json=Mock(return_value=dados))


@override_settings(
    CREDENTIALS_ENCRYPTION_KEY=CHAVE_FERNET, WOOVI_ONBOARDING_APP_ID=PARCEIRO,
    SITE_URL="https://sistema.example.com",
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
)
class PartnerTests(CenarioWoovi, TestCase):
    def setUp(self):
        self.criar_cenario()
        self.academia.cnpj = CNPJ
        self.academia.save(update_fields=["cnpj"])
        self.propria = preparar_conta(self.academia, self.usuario)
        # Cadastro aberto pelo sistema (ciência da taxa registrada) e aprovado.
        ContaRecebimento.objects.filter(pk=self.propria.pk).update(
            onboarding_url=KYC, onboarding_status="APPROVED", status=ContaRecebimento.AGUARDANDO,
            taxa_ciente_em=timezone.now(),
        )
        self.propria.refresh_from_db()
        self.chamadas = []

    # Woovi falsa: responde por rota e registra cada chamada.
    def woovi(self, *, cadastro="APPROVED", application=None, status_application=201, cnpj_conta=CNPJ):
        def responder(metodo, url, **kw):
            self.chamadas.append((metodo, url, kw))
            if url.endswith("/api/v1/partner/application"):
                corpo = application if application is not None else {"application": {
                    "name": "Keiko", "isActive": True, "type": "API",
                    "clientId": CLIENT_ID, "clientSecret": CLIENT_SECRET,
                }}
                return http(corpo, status_application)
            if url.endswith("/api/v1/kyc/onboarding"):
                return http({"linkOnboarding": KYC, "accountRegister": {
                    "correlationID": kw["json"]["correlationID"], "taxID": {"taxID": CNPJ}, "status": "PENDING"}}, 201)
            if "/api/v1/account-register/" in url:
                return http({"correlationID": self.propria.onboarding_correlation_id,
                             "taxID": {"taxID": CNPJ}, "status": cadastro})
            if url.endswith("/api/v1/account"):
                return http({"accounts": [{"accountId": "conta-1", "isDefault": True}]})
            if "/api/v1/account/" in url:
                return http({"account": {"accountId": "conta-1", "taxId": cnpj_conta}})
            if url.endswith("/api/v1/webhook") and metodo == "GET":
                return http({"webhooks": []})
            if url.endswith("/api/v1/webhook") and metodo == "POST":
                return http({"webhook": kw["json"]["webhook"]})
            raise AssertionError(f"rota inesperada: {metodo} {url}")
        return responder

    def rotas(self, metodo=None):
        return [url.split("/api/", 1)[1] for m, url, _ in self.chamadas if metodo in (None, m)]

    # ----------------------------------------------------------- AppID e cifra
    def test_app_id_e_base64_de_client_id_e_client_secret(self):
        self.assertEqual(partner.montar_app_id(CLIENT_ID, CLIENT_SECRET), APP_ID)
        self.assertEqual(base64.b64decode(APP_ID).decode(), f"{CLIENT_ID}:{CLIENT_SECRET}")
        for invalido in (("", CLIENT_SECRET), (CLIENT_ID, ""), (None, CLIENT_SECRET), ("a:b", CLIENT_SECRET)):
            with self.assertRaises(WooviInvalidResponseError):
                partner.montar_app_id(*invalido)

    def test_cifra_nao_guarda_texto_puro_e_exige_a_chave(self):
        cifrada = cifrar(APP_ID)
        self.assertNotIn(APP_ID, cifrada)
        self.assertNotIn(CLIENT_SECRET, cifrada)
        self.assertEqual(decifrar(cifrada), APP_ID)
        with override_settings(CREDENTIALS_ENCRYPTION_KEY=""):
            with self.assertRaises(WooviConfigError):
                cifrar(APP_ID)
        with override_settings(CREDENTIALS_ENCRYPTION_KEY=Fernet.generate_key().decode()):
            with self.assertRaises(WooviConfigError) as erro:
                decifrar(cifrada)
        self.assertNotIn(APP_ID, str(erro.exception))

    def test_escopos_da_afiliada_nunca_movimentam_dinheiro(self):
        self.assertEqual(partner.validar_escopos(partner.ESCOPOS_AFILIADA), partner.ESCOPOS_AFILIADA)
        for proibido in (["WITHDRAW_POST"], ["TRANSFER_POST"], ["CHARGE_POST", "SUBACCOUNT_DEBIT"], []):
            with self.assertRaises(ValueError):
                partner.validar_escopos(proibido)

    def test_rotas_de_parceiro_so_com_a_credencial_de_parceiro(self):
        with override_settings(**{referencia_academia(self.academia.pk): "segredo-academia"}):
            client = WooviClient(conta=self.propria)
        with self.assertRaises(WooviConfigError):
            client.criar_application_afiliada(cnpj=CNPJ, nome="x", escopos=partner.ESCOPOS_AFILIADA)

    # --------------------------------------------------------- fluxo completo
    @patch("integracoes.woovi.client.requests.request")
    def test_aprovado_cria_application_guarda_cifrada_configura_webhook_e_ativa(self, request):
        request.side_effect = self.woovi()
        with self.assertLogs("integracoes.woovi.contas", "INFO") as logs:
            conta = conectar_automaticamente(self.propria, self.usuario)

        self.assertEqual(conta.status, ContaRecebimento.CONECTADA)
        self.assertTrue(conta.ativa)
        self.assertEqual(conta.etapa, partner.ATIVA)
        self.assertIsNotNone(conta.webhook_configurado_em)
        self.assertEqual(conta.credencial_fingerprint, fingerprint(APP_ID))
        self.assertEqual(decifrar(conta.credencial_cifrada), APP_ID)
        self.assertEqual(da_conta(conta), APP_ID)

        application = next(kw for m, url, kw in self.chamadas if url.endswith("/partner/application"))
        self.assertEqual(application["headers"]["Authorization"], PARCEIRO)
        self.assertEqual(application["json"]["taxID"], {"taxID": CNPJ, "type": "BR:CNPJ"})
        self.assertEqual(application["json"]["application"]["type"], "API")
        self.assertEqual(tuple(application["json"]["application"]["scopes"]), partner.ESCOPOS_AFILIADA)
        # Tudo depois da application usa a credencial da própria afiliada.
        for metodo, url, kw in self.chamadas:
            if "/partner/" not in url and "account-register" not in url:
                self.assertEqual(kw["headers"]["Authorization"], APP_ID)
        webhook = next(kw for m, url, kw in self.chamadas if m == "POST" and url.endswith("/webhook"))
        self.assertEqual(webhook["json"]["webhook"]["url"], f"https://sistema.example.com/webhooks/woovi/contas/{conta.pk}/")

        # Segredo nunca em texto puro no banco nem no log.
        for valor in ContaRecebimento.objects.values_list(named=False):
            texto = " ".join(str(v) for v in valor)
            self.assertNotIn(CLIENT_SECRET, texto)
            self.assertNotIn(APP_ID, texto)
        self.assertNotIn(CLIENT_SECRET, "".join(logs.output))
        self.assertNotIn(APP_ID, "".join(logs.output))

    @patch("integracoes.woovi.client.requests.request")
    def test_repetir_nao_cria_outra_application(self, request):
        request.side_effect = self.woovi()
        ContaRecebimento.objects.filter(pk=self.propria.pk).update(credencial_cifrada=cifrar(APP_ID))
        self.propria.refresh_from_db()
        self.assertEqual(self.propria.etapa, partner.APPLICATION_CRIADA)
        conta = conectar_automaticamente(self.propria)
        self.assertNotIn("v1/partner/application", self.rotas())
        self.chamadas.clear()
        self.assertEqual(conectar_automaticamente(conta), conta)
        self.assertEqual(self.chamadas, [])

    @patch("integracoes.woovi.client.requests.request")
    def test_kyc_nao_aprovado_nao_cria_application(self, request):
        ContaRecebimento.objects.filter(pk=self.propria.pk).update(onboarding_status="IN_REVIEW")
        self.propria.refresh_from_db()
        self.assertEqual(self.propria.etapa, partner.EM_ANALISE)
        request.side_effect = self.woovi(cadastro="IN_REVIEW")
        with self.assertRaises(ValueError):
            conectar_automaticamente(self.propria)
        self.assertNotIn("v1/partner/application", self.rotas())
        self.propria.refresh_from_db()
        self.assertEqual(self.propria.credencial_cifrada, "")
        self.assertIsNone(self.propria.conectada_em)

    def test_etapas_do_cadastro(self):
        for status, etapa in (("PENDING", partner.AGUARDANDO_KYC), ("IN_REVIEW", partner.EM_ANALISE),
                              ("APPROVED", partner.APROVADO), ("REJECTED", partner.ERRO), ("", partner.AGUARDANDO_KYC)):
            self.assertEqual(partner.etapa_do_cadastro(status), etapa)
        nova = ContaRecebimento(academia=self.academia, modelo_recebimento=ContaRecebimento.CONTA_PROPRIA)
        self.assertEqual(nova.etapa, partner.NAO_INICIADO)

    @patch("integracoes.woovi.client.requests.request")
    def test_partner_nao_habilitada_nao_guarda_nada_e_fallback_do_operador_funciona(self, request):
        request.side_effect = self.woovi(application={"error": "Forbidden " + PARCEIRO}, status_application=403)
        with self.assertRaises(WooviAuthError) as erro:
            conectar_automaticamente(self.propria)
        self.assertNotIn(PARCEIRO, str(erro.exception))
        self.propria.refresh_from_db()
        self.assertEqual(self.propria.credencial_cifrada, "")
        self.assertIsNone(self.propria.conectada_em)

        # Fallback: operador provisiona a credencial no servidor.
        self.chamadas.clear()
        request.side_effect = self.woovi()
        with override_settings(**{referencia_academia(self.academia.pk): "appid-do-operador"}):
            conta = conectar_conta(self.propria, self.usuario)
            self.assertEqual(conta.status, ContaRecebimento.CONECTADA)
            self.assertEqual(conta.credencial_fingerprint, fingerprint("appid-do-operador"))
        self.assertNotIn("v1/partner/application", self.rotas())

    def test_sem_automacao_nem_credencial_pede_preparo_no_servidor(self):
        with override_settings(WOOVI_ONBOARDING_APP_ID=""):
            with self.assertRaises(WooviConfigError):
                conectar_conta(self.propria)

    @patch("integracoes.woovi.client.requests.request")
    def test_mesma_credencial_em_outra_conta_e_recusada(self, request):
        outra = Academia.objects.create(nome="Outra escola", cnpj="11222333000181")
        ContaRecebimento.objects.create(
            academia=outra, modelo_recebimento=ContaRecebimento.CONTA_PROPRIA, ativa=False,
            credencial_ref=referencia_academia(outra.pk), credencial_fingerprint=fingerprint(APP_ID),
        )
        request.side_effect = self.woovi()
        with self.assertRaises(ValueError):
            conectar_automaticamente(self.propria)
        self.propria.refresh_from_db()
        self.assertEqual(self.propria.credencial_cifrada, "")
        self.assertFalse(self.propria.ativa)

    @patch("integracoes.woovi.client.requests.request")
    def test_cnpj_da_conta_diferente_nao_ativa(self, request):
        request.side_effect = self.woovi(cnpj_conta="99888777000166")
        with self.assertRaises(ValueError):
            conectar_automaticamente(self.propria)
        self.propria.refresh_from_db()
        self.assertFalse(self.propria.ativa)
        self.assertIsNone(self.propria.conectada_em)
        self.assertNotIn("v1/webhook", self.rotas("POST"))

    @patch("integracoes.woovi.client.requests.request")
    def test_resposta_sem_segredo_nao_conecta(self, request):
        request.side_effect = self.woovi(application={"application": {"clientId": CLIENT_ID, "isActive": True}})
        with self.assertRaises(WooviInvalidResponseError):
            conectar_automaticamente(self.propria)
        self.propria.refresh_from_db()
        self.assertEqual(self.propria.credencial_cifrada, "")

    @patch("integracoes.woovi.client.requests.request")
    def test_credencial_da_conta_conectada_nao_pode_ser_trocada(self, request):
        request.side_effect = self.woovi()
        conta = conectar_automaticamente(self.propria)
        conta.credencial_cifrada = cifrar("outro-appid")
        with self.assertRaises(ValidationError):
            conta.save()

    def test_admin_nunca_exibe_a_credencial(self):
        modelo_admin = admin.site._registry[ContaRecebimento]
        self.assertIn("credencial_cifrada", modelo_admin.exclude)
        self.assertNotIn("credencial_cifrada", modelo_admin.readonly_fields)

    # ------------------------------------------------------- tela e comando
    @patch("integracoes.woovi.client.requests.request")
    def test_tela_atualizar_conclui_a_conexao_quando_aprovado(self, request):
        request.side_effect = self.woovi()
        AcessoAcademia.objects.create(usuario=self.usuario, academia=self.academia, administrador=True)
        self.client.force_login(self.usuario)
        resposta = self.client.post("/configuracoes/recebimento/", {"acao": "atualizar", "confirmacao": "on"}, follow=True)
        self.assertContains(resposta, "Cadastro aprovado e conta Woovi conectada")
        self.assertNotContains(resposta, CLIENT_SECRET)
        self.propria.refresh_from_db()
        self.assertEqual(self.propria.status, ContaRecebimento.CONECTADA)

    @patch("integracoes.woovi.client.requests.request")
    def test_tela_partner_nao_habilitada_orienta_fallback_sem_marcar_erro(self, request):
        request.side_effect = self.woovi(status_application=403, application={"error": "Forbidden"})
        AcessoAcademia.objects.create(usuario=self.usuario, academia=self.academia, administrador=True)
        self.client.force_login(self.usuario)
        resposta = self.client.post("/configuracoes/recebimento/", {"acao": "ativar", "confirmacao": "on"}, follow=True)
        self.assertContains(resposta, "o suporte conclui a conexão pelo servidor")
        self.propria.refresh_from_db()
        self.assertNotEqual(self.propria.status, ContaRecebimento.ERRO)

    @patch("integracoes.woovi.client.requests.request")
    def test_comando_conecta_as_aprovadas(self, request):
        request.side_effect = self.woovi()
        saida = StringIO()
        call_command("conectar_contas_woovi", stdout=saida, stderr=StringIO())
        self.assertIn("1 conectada(s)", saida.getvalue())
        self.assertNotIn(CLIENT_SECRET, saida.getvalue())
        self.propria.refresh_from_db()
        self.assertEqual(self.propria.status, ContaRecebimento.CONECTADA)

    # ------------------------------------------------ passos da tela e ciência
    def entrar(self):
        AcessoAcademia.objects.create(usuario=self.usuario, academia=self.academia, administrador=True)
        self.client.force_login(self.usuario)
        return "/configuracoes/recebimento/"

    def test_conexao_automatica_exige_a_ciencia_da_taxa(self):
        ContaRecebimento.objects.filter(pk=self.propria.pk).update(taxa_ciente_em=None)
        self.propria.refresh_from_db()
        with patch("integracoes.woovi.client.requests.request") as request:
            with self.assertRaises(ValueError):
                conectar_automaticamente(self.propria)
            request.assert_not_called()

    def test_tela_sem_conta_com_parceiro_leva_a_abrir_conta(self):
        url = self.entrar()
        self.propria.delete()
        resposta = self.client.get(url)
        self.assertContains(resposta, "Passos da conexão")
        self.assertContains(resposta, 'value="iniciar">Abrir conta na Woovi')
        self.assertContains(resposta, "Estou ciente da taxa")
        self.assertNotContains(resposta, "Verificar e ativar")
        self.assertNotContains(resposta, "Abrir cadastro oficial da Woovi")
        self.assertNotContains(resposta, "solicite ao suporte")

    def test_tela_sem_parceiro_oferece_o_fallback(self):
        url = self.entrar()
        self.propria.delete()
        with override_settings(WOOVI_ONBOARDING_APP_ID=""):
            resposta = self.client.get(url)
        self.assertContains(resposta, "Abrir cadastro oficial da Woovi")
        self.assertContains(resposta, "Verificar e ativar")
        self.assertNotContains(resposta, 'value="iniciar"')

    def test_tela_em_analise_nao_pede_ciencia_de_novo(self):
        url = self.entrar()
        ContaRecebimento.objects.filter(pk=self.propria.pk).update(onboarding_status="IN_REVIEW")
        resposta = self.client.get(url)
        self.assertContains(resposta, "Continuar cadastro na Woovi")
        self.assertContains(resposta, "Atualizar situação")
        self.assertContains(resposta, "A Woovi está analisando o cadastro")
        self.assertNotContains(resposta, "Estou ciente da taxa")
        self.assertNotContains(resposta, "Verificar e ativar")

    def test_tela_aprovado_mostra_concluir_conexao(self):
        url = self.entrar()
        resposta = self.client.get(url)
        self.assertContains(resposta, "Concluir conexão")
        self.assertContains(resposta, "Cadastro aprovado pela Woovi")
        self.assertNotContains(resposta, 'value="iniciar"')

    @patch("integracoes.woovi.client.requests.request")
    def test_abrir_conta_exige_e_registra_a_ciencia(self, request):
        request.side_effect = self.woovi()
        url = self.entrar()
        self.propria.delete()
        resposta = self.client.post(url, {"acao": "iniciar"}, follow=True)
        self.assertContains(resposta, "Confirme a ciência da taxa")
        request.assert_not_called()
        self.assertFalse(ContaRecebimento.objects.filter(modelo_recebimento=ContaRecebimento.CONTA_PROPRIA).exists())

        self.client.post(url, {"acao": "iniciar", "confirmacao": "on"}, follow=True)
        conta = ContaRecebimento.objects.get(modelo_recebimento=ContaRecebimento.CONTA_PROPRIA)
        self.assertIsNotNone(conta.taxa_ciente_em)
        self.assertEqual(conta.onboarding_url, KYC)
        self.assertEqual(conta.etapa, partner.AGUARDANDO_KYC)
