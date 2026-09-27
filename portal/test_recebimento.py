from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from academias.models import Academia
from financeiro.models import ContaRecebimento, Repasse
from integracoes.woovi.exceptions import WooviUnavailableError
from portal.models import AcessoAcademia
from tests.woovi_base import subconta

TERMOS_INTERNOS = ("Woovi", "woovi", "OpenPix", "openpix", "subconta", "Subconta", "withdraw", "saque", "saldo")


class RecebimentoTests(TestCase):
    def setUp(self):
        self.a = Academia.objects.create(nome="Keiko", nome_fantasia="Escola de Judô Keiko Fukuda", cnpj="RC1")
        usuarios = get_user_model().objects
        self.dona = usuarios.create_user("dona", password="senha123")
        AcessoAcademia.objects.create(usuario=self.dona, academia=self.a, administrador=True)
        self.operador = usuarios.create_user("operador", password="senha123")
        AcessoAcademia.objects.create(usuario=self.operador, academia=self.a)
        self.client.force_login(self.dona)

    def salvar(self, tipo="email", chave="escola@keiko.com.br", confirmacao=True):
        dados = {"tipo_chave": tipo, "chave": chave}
        if confirmacao:
            dados["confirmacao"] = "on"
        return self.client.post("/configuracoes/recebimento/", dados, follow=True)

    def conta(self, **extra):
        return ContaRecebimento.objects.create(
            academia=self.a, tipo_chave=ContaRecebimento.EMAIL, pix_key="escola@keiko.com.br", **extra,
        )

    # ------------------------------------------------------------- acesso
    def test_so_o_administrador_da_academia_acessa(self):
        self.assertEqual(self.client.get("/configuracoes/recebimento/").status_code, 200)
        self.client.force_login(self.operador)
        self.assertEqual(self.client.get("/configuracoes/recebimento/").status_code, 403)

    def test_link_no_menu_so_para_o_administrador(self):
        self.assertContains(self.client.get("/painel/"), "/configuracoes/recebimento/")
        self.client.force_login(self.operador)
        self.assertNotContains(self.client.get("/painel/"), "/configuracoes/recebimento/")

    # ------------------------------------------------------------- cadastro
    @patch("integracoes.woovi.services.WooviClient")
    def test_cadastra_a_chave_pix(self, mock_client):
        mock_client.return_value.criar_ou_obter_subconta.return_value = subconta()

        resposta = self.salvar(chave="Escola@Keiko.com.br")

        self.assertContains(resposta, "Chave Pix de recebimento salva.")
        conta = ContaRecebimento.ativa_da(self.a)
        self.assertEqual((conta.pix_key, conta.criada_por), ("escola@keiko.com.br", self.dona))

    @patch("integracoes.woovi.services.WooviClient")
    def test_chave_invalida_mostra_erro_sem_chamar_o_provedor(self, mock_client):
        resposta = self.salvar(tipo="cpf", chave="123.456.789-00")
        self.assertContains(resposta, "CPF inválido")
        mock_client.assert_not_called()
        self.assertFalse(ContaRecebimento.objects.exists())

    @patch("integracoes.woovi.services.WooviClient")
    def test_exige_confirmacao(self, mock_client):
        self.salvar(confirmacao=False)
        mock_client.assert_not_called()
        self.assertFalse(ContaRecebimento.objects.exists())

    @patch("integracoes.woovi.services.WooviClient")
    def test_falha_do_provedor_vira_mensagem_generica(self, mock_client):
        mock_client.return_value.criar_ou_obter_subconta.side_effect = WooviUnavailableError(
            "A Woovi retornou o status HTTP 502."
        )
        with self.assertLogs("portal.views_recebimento", level="WARNING"):
            resposta = self.salvar()
        self.assertContains(resposta, "Não foi possível validar esta chave Pix agora")
        self.assertNotContains(resposta, "Woovi")
        self.assertNotContains(resposta, "502")

    @patch("integracoes.woovi.services.WooviClient")
    def test_troca_bloqueada_com_transferencia_em_andamento(self, mock_client):
        conta = self.conta()
        Repasse.objects.create(academia=self.a, conta_recebimento=conta, pix_key_destino=conta.pix_key)
        resposta = self.salvar(chave="nova@keiko.com.br")
        self.assertContains(resposta, "transferência de valores em andamento")
        mock_client.assert_not_called()

    @patch("integracoes.woovi.services.WooviClient")
    def test_troca_mostra_a_chave_anterior_no_historico(self, mock_client):
        self.conta()
        mock_client.return_value.criar_ou_obter_subconta.return_value = subconta(chave="nova@keiko.com.br")

        resposta = self.salvar(chave="nova@keiko.com.br")

        self.assertContains(resposta, "Chaves anteriores")
        self.assertContains(resposta, "escola@keiko.com.br")
        self.assertEqual(ContaRecebimento.ativa_da(self.a).pix_key, "nova@keiko.com.br")

    # --------------------------------------------------------------- status
    def test_status_simples_sem_termos_internos(self):
        resposta = self.client.get("/configuracoes/recebimento/")
        self.assertContains(resposta, "Não configurado")

        conta = self.conta()
        resposta = self.client.get("/configuracoes/recebimento/")
        self.assertContains(resposta, "Recebimento ativo")

        repasse = Repasse.objects.create(academia=self.a, conta_recebimento=conta, pix_key_destino=conta.pix_key)
        self.assertContains(self.client.get("/configuracoes/recebimento/"), "Transferência em andamento")

        Repasse.objects.filter(pk=repasse.pk).update(status=Repasse.REQUER_ATENCAO)
        resposta = self.client.get("/configuracoes/recebimento/")
        self.assertContains(resposta, "Precisa de atenção")
        for termo in TERMOS_INTERNOS:
            self.assertNotContains(resposta, termo)

    def test_mostra_a_ultima_transferencia(self):
        conta = self.conta()
        Repasse.objects.create(
            academia=self.a, conta_recebimento=conta, pix_key_destino=conta.pix_key,
            status=Repasse.CONCLUIDA, valor=Decimal("237.55"), concluido_em=timezone.now(),
        )
        self.assertContains(self.client.get("/configuracoes/recebimento/"), "R$ 237")


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
