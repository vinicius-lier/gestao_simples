from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from academias.models import Academia
from financeiro.models import ContaRecebimento, Repasse
from integracoes.woovi.exceptions import WooviUnavailableError
from portal.models import AcessoAcademia
from integracoes.woovi.contas import EXPLICACAO_TAXA, TEXTO_TAXA, preparar_conta
from integracoes.woovi.exceptions import WooviConfigError

@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class RecebimentoTests(TestCase):
    def setUp(self):
        self.a = Academia.objects.create(nome="Keiko", cnpj="12345678000195")
        self.dona = get_user_model().objects.create_user("dona", password="senha123")
        self.operador = get_user_model().objects.create_user("operador", password="senha123")
        AcessoAcademia.objects.create(usuario=self.dona, academia=self.a, administrador=True)
        AcessoAcademia.objects.create(usuario=self.operador, academia=self.a)
        self.client.force_login(self.dona)
        self.url = "/configuracoes/recebimento/"

    def salvar(self, acao="ativar", confirmar=True):
        return self.client.post(self.url, {"acao": acao, **({"confirmacao": "on"} if confirmar else {})}, follow=True)

    def test_so_administrador_acessa_e_ve_link_no_menu(self):
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.assertContains(self.client.get("/painel/"), self.url)
        self.client.force_login(self.operador)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertNotContains(self.client.get("/painel/"), self.url)

    def test_taxa_comercial_visivel_antes_e_depois_conexao(self):
        resposta = self.client.get(self.url)
        self.assertContains(resposta, "Não configurada")
        for texto in (TEXTO_TAXA, EXPLICACAO_TAXA, "Conta Woovi da Academia"):
            self.assertContains(resposta, texto)
        conta = preparar_conta(self.a)
        ContaRecebimento.objects.filter(pk=conta.pk).update(status=ContaRecebimento.CONECTADA, conectada_em=timezone.now())
        resposta = self.client.get(self.url)
        self.assertContains(resposta, TEXTO_TAXA)
        self.assertContains(resposta, EXPLICACAO_TAXA)
        self.assertContains(resposta, "Conectada")
        self.assertNotContains(resposta, "Verificar e ativar")

    def test_todos_os_estados_sao_consultaveis(self):
        conta = preparar_conta(self.a)
        for estado, texto in ContaRecebimento.STATUS:
            ContaRecebimento.objects.filter(pk=conta.pk).update(status=estado)
            self.assertContains(self.client.get(self.url), texto)

    @patch("portal.views_recebimento.ativar_conta")
    def test_exige_ciencia_da_taxa(self, ativar):
        resposta = self.salvar(confirmar=False)
        self.assertContains(resposta, "Confirme a ciência da taxa")
        ativar.assert_not_called()
        self.assertFalse(ContaRecebimento.objects.exists())

    @patch("portal.views_recebimento.ativar_conta")
    def test_acao_ativar_usa_conta_da_academia(self, ativar):
        self.salvar()
        conta = ContaRecebimento.objects.get()
        self.assertEqual(conta.modelo_recebimento, ContaRecebimento.CONTA_PROPRIA)
        ativar.assert_called_once_with(conta, self.dona)

    @patch("portal.views_recebimento.ativar_conta")
    def test_erro_externo_e_logs_nao_expoem_token(self, ativar):
        segredo = "AppID-super-secreto-testando"
        ativar.side_effect = WooviUnavailableError(segredo)
        with self.assertLogs("portal.views_recebimento", level="WARNING") as logs:
            resposta = self.salvar()
        self.assertNotContains(resposta, segredo)
        self.assertNotIn(segredo, " ".join(logs.output))
        self.assertContains(resposta, "Não foi possível validar a conexão agora")
        self.assertEqual(ContaRecebimento.objects.get().status, ContaRecebimento.ERRO)

    @patch("portal.views_recebimento.ativar_conta", side_effect=WooviConfigError("token-secreto"))
    def test_credencial_ausente_orienta_suporte_sem_expor(self, ativar):
        resposta = self.salvar()
        self.assertContains(resposta, "A conexão precisa ser preparada no servidor")
        self.assertNotContains(resposta, "token-secreto")

    @override_settings(WOOVI_ONBOARDING_APP_ID="onboarding-secreto")
    @patch("portal.views_recebimento.iniciar_onboarding")
    def test_inicia_onboarding_pelo_sistema(self, iniciar):
        self.salvar("iniciar")
        iniciar.assert_called_once_with(ContaRecebimento.objects.get())

    @patch("portal.views_recebimento.iniciar_onboarding")
    def test_sem_api_habilitada_mostra_cadastro_oficial(self, iniciar):
        resposta = self.salvar("iniciar")
        iniciar.assert_not_called()
        self.assertContains(resposta, "cadastro oficial")
        self.assertFalse(ContaRecebimento.objects.get().ativa)

    def test_link_kyc_falso_nao_aparece_e_resposta_nao_e_cacheada(self):
        conta = preparar_conta(self.a)
        conta.onboarding_url = "https://evil.example/onboarding/segredo"
        conta.save()
        resposta = self.client.get(self.url)
        self.assertNotContains(resposta, "evil.example")
        self.assertNotContains(resposta, conta.credencial_ref)
        self.assertNotContains(resposta, 'name="app_id"')
        self.assertIn("no-store", resposta["Cache-Control"])
        self.assertEqual(resposta["Referrer-Policy"], "no-referrer")

    def test_historico_e_ultima_transferencia_permanecem(self):
        antiga = ContaRecebimento.objects.create(academia=self.a, pix_key="antiga@exemplo.com", tipo_chave="email")
        Repasse.objects.create(academia=self.a, conta_recebimento=antiga, pix_key_destino=antiga.pix_key,
                              status=Repasse.CONCLUIDA, valor=Decimal("237.55"), concluido_em=timezone.now())
        preparar_conta(self.a)
        resposta = self.client.get(self.url)
        self.assertContains(resposta, "Recebimentos anteriores")
        self.assertContains(resposta, antiga.pix_key)
        self.assertContains(resposta, "R$ 237")

    def test_dados_de_outra_academia_nao_sao_exibidos(self):
        outra = Academia.objects.create(nome="Outra", cnpj="OUTRA")
        ContaRecebimento.objects.create(academia=outra, pix_key="privado@outra.com", tipo_chave="email")
        self.assertNotContains(self.client.get(self.url), "privado@outra.com")


class AvisoPlataformaTests(TestCase):
    def setUp(self):
        self.a = Academia.objects.create(nome="Keiko", cnpj="RC2")
        conta = ContaRecebimento.objects.create(academia=self.a, tipo_chave=ContaRecebimento.EMAIL, pix_key="e@k.com")
        Repasse.objects.create(
            academia=self.a, conta_recebimento=conta, pix_key_destino="e@k.com", status=Repasse.REQUER_ATENCAO,
        )
        usuarios = get_user_model().objects
        self.plataforma = usuarios.create_superuser("plataforma", password="senha123")
        AcessoAcademia.objects.create(usuario=self.plataforma, academia=self.a, administrador=True)
        self.dona = usuarios.create_user("dona", password="senha123")
        academia_b = Academia.objects.create(nome="Outra", cnpj="RC3")
        AcessoAcademia.objects.create(usuario=self.dona, academia=academia_b, administrador=True)

    def test_superusuario_ve_o_aviso(self):
        self.client.force_login(self.plataforma)
        self.assertContains(self.client.get("/painel/"), "precisa de atenção")

    def test_academia_nao_ve_o_aviso(self):
        self.client.force_login(self.dona)
        self.assertNotContains(self.client.get("/painel/"), "precisa de atenção")
