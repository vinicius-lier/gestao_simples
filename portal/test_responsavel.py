from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from academias.models import Academia
from atletas.models import Atleta, Responsavel
from financeiro.models import Mensalidade
from matriculas.models import Matricula
from modalidades.models import Modalidade
from portal.models import AcessoAcademia, TokenAcessoResponsavel


class TokenAcessoResponsavelTests(TestCase):
    def setUp(self):
        self.academia = Academia.objects.create(nome="Academia Teste", cnpj="TR1")
        self.responsavel = Responsavel.objects.create(
            academia=self.academia, nome="Maria", cpf="1", whatsapp="21999999999"
        )

    def test_gerar_cria_token_valido_por_24h(self):
        acesso = TokenAcessoResponsavel.gerar(self.responsavel)
        self.assertTrue(acesso.valido)
        self.assertTrue(acesso.token)
        self.assertGreater(acesso.expira_em, timezone.now() + timedelta(hours=23))

    def test_consumir_invalida_o_token(self):
        acesso = TokenAcessoResponsavel.gerar(self.responsavel)
        acesso.consumir()
        self.assertFalse(acesso.valido)

    def test_token_expirado_e_invalido(self):
        acesso = TokenAcessoResponsavel.gerar(self.responsavel)
        acesso.expira_em = timezone.now() - timedelta(minutes=1)
        acesso.save(update_fields=["expira_em"])
        self.assertFalse(acesso.valido)


class ResponsavelPortalTests(TestCase):
    def setUp(self):
        self.a = Academia.objects.create(nome="A", cnpj="RPA1")
        self.b = Academia.objects.create(nome="B", cnpj="RPB1")
        self.responsavel = Responsavel.objects.create(
            academia=self.a, nome="Maria", cpf="1", whatsapp="21999999999"
        )
        self.outro_responsavel = Responsavel.objects.create(
            academia=self.a, nome="Outro", cpf="2", whatsapp="21988888888"
        )
        self.atleta = Atleta.objects.create(
            academia=self.a, nome="Filho da Maria", responsavel_financeiro=self.responsavel
        )
        self.modalidade = Modalidade.objects.create(academia=self.a, nome="Judô")
        self.matricula = Matricula.objects.create(
            academia=self.a, atleta=self.atleta, modalidade=self.modalidade,
            valor_mensalidade=Decimal("120.00"), dia_vencimento=10, data_inicio=date(2026, 1, 1),
        )
        self.mensalidade = Mensalidade.objects.create(
            academia=self.a, matricula=self.matricula, competencia=date(2026, 9, 1),
            valor=Decimal("120.00"), vencimento=date(2999, 1, 1), status="pendente",
        )

    def test_link_valido_abre_sessao_e_e_consumido(self):
        acesso = TokenAcessoResponsavel.gerar(self.responsavel)
        resposta = self.client.post(f"/responsavel/entrar/{acesso.token}/")
        self.assertRedirects(resposta, "/responsavel/")
        self.assertEqual(self.client.session.get("responsavel_id"), self.responsavel.pk)
        acesso.refresh_from_db()
        self.assertIsNotNone(acesso.usado_em)

    def test_link_com_next_valido_vai_direto_para_pagar(self):
        acesso = TokenAcessoResponsavel.gerar(self.responsavel)
        destino = f"/responsavel/mensalidade/{self.mensalidade.pk}/pagar/"
        resposta = self.client.post(f"/responsavel/entrar/{acesso.token}/?next={destino}")
        self.assertRedirects(resposta, destino)

    def test_link_com_next_de_outra_familia_cai_no_painel(self):
        outro_responsavel = Responsavel.objects.create(
            academia=self.a, nome="Estranho", cpf="3", whatsapp="21977776666"
        )
        acesso = TokenAcessoResponsavel.gerar(outro_responsavel)
        destino = f"/responsavel/mensalidade/{self.mensalidade.pk}/pagar/"
        resposta = self.client.post(f"/responsavel/entrar/{acesso.token}/?next={destino}")
        self.assertRedirects(resposta, "/responsavel/")

    def test_link_com_next_externo_e_ignorado(self):
        acesso = TokenAcessoResponsavel.gerar(self.responsavel)
        resposta = self.client.post(f"/responsavel/entrar/{acesso.token}/?next=https://evil.example.com/")
        self.assertRedirects(resposta, "/responsavel/")

    def test_link_usado_duas_vezes_falha_na_segunda(self):
        acesso = TokenAcessoResponsavel.gerar(self.responsavel)
        self.client.post(f"/responsavel/entrar/{acesso.token}/")
        self.client.logout()
        resposta = self.client.post(f"/responsavel/entrar/{acesso.token}/")
        self.assertEqual(resposta.status_code, 410)

    def test_abrir_o_link_mostra_tela_de_entrada_sem_consumir(self):
        # Prévia de link do WhatsApp e antivírus fazem GET: não podem
        # queimar o token antes da família clicar.
        acesso = TokenAcessoResponsavel.gerar(self.responsavel)
        resposta = self.client.get(f"/responsavel/entrar/{acesso.token}/")
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Entrar")
        self.assertNotIn("responsavel_id", self.client.session)
        acesso.refresh_from_db()
        self.assertIsNone(acesso.usado_em)

    def test_tela_de_entrada_preserva_o_next_no_formulario(self):
        acesso = TokenAcessoResponsavel.gerar(self.responsavel)
        destino = f"/responsavel/mensalidade/{self.mensalidade.pk}/pagar/"
        resposta = self.client.get(f"/responsavel/entrar/{acesso.token}/?next={destino}")
        self.assertContains(resposta, f'action="/responsavel/entrar/{acesso.token}/?next={destino}"')

    def test_reabrir_link_usado_no_mesmo_navegador_segue_logado(self):
        acesso = TokenAcessoResponsavel.gerar(self.responsavel)
        destino = f"/responsavel/mensalidade/{self.mensalidade.pk}/pagar/"
        self.client.post(f"/responsavel/entrar/{acesso.token}/")
        resposta = self.client.get(f"/responsavel/entrar/{acesso.token}/?next={destino}")
        self.assertRedirects(resposta, destino, fetch_redirect_response=False)

    def test_link_expirado_da_410_mesmo_no_get(self):
        acesso = TokenAcessoResponsavel.gerar(self.responsavel)
        acesso.expira_em = timezone.now() - timedelta(minutes=1)
        acesso.save(update_fields=["expira_em"])
        self.assertEqual(self.client.get(f"/responsavel/entrar/{acesso.token}/").status_code, 410)

    def test_link_inexistente_da_404(self):
        self.assertEqual(self.client.get("/responsavel/entrar/token-invalido/").status_code, 404)

    def test_painel_exige_sessao_valida(self):
        resposta = self.client.get("/responsavel/", follow=True)
        self.assertRedirects(resposta, "/responsavel/link-expirado/")

    def _logar(self, responsavel):
        acesso = TokenAcessoResponsavel.gerar(responsavel)
        self.client.post(f"/responsavel/entrar/{acesso.token}/")

    def test_painel_mostra_so_os_alunos_do_proprio_responsavel(self):
        self._logar(self.responsavel)
        resposta = self.client.get("/responsavel/")
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Filho da Maria")

        self._logar(self.outro_responsavel)
        resposta = self.client.get("/responsavel/")
        self.assertNotContains(resposta, "Filho da Maria")

    def test_nao_acessa_mensalidade_de_outra_familia(self):
        self._logar(self.outro_responsavel)
        resposta = self.client.get(f"/responsavel/mensalidade/{self.mensalidade.pk}/pagar/")
        self.assertEqual(resposta.status_code, 404)

    def _com_chave(self):
        from financeiro.models import ContaRecebimento

        ContaRecebimento.objects.create(
            academia=self.a, tipo_chave=ContaRecebimento.EMAIL, pix_key="escola@exemplo.com",
        )

    @patch("integracoes.woovi.services.WooviClient")
    def test_pagar_gera_pix_e_mostra_qrcode_sem_nada_do_provedor(self, mock_client_class):
        from tests.woovi_base import cobranca_criada

        self._com_chave()
        mock_client_class.return_value.criar_cobranca.side_effect = cobranca_criada
        self._logar(self.responsavel)

        resposta = self.client.get(f"/responsavel/mensalidade/{self.mensalidade.pk}/pagar/")

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "00020126PIX")
        self.assertContains(resposta, "<svg")
        for termo in ("woovi", "Woovi", "openpix", "OpenPix", "subconta"):
            self.assertNotContains(resposta, termo)
        mock_client_class.return_value.criar_cobranca.assert_called_once()

    @patch("integracoes.woovi.services.WooviClient")
    def test_pagar_reaproveita_pix_vigente(self, mock_client_class):
        from tests.woovi_base import cobranca_criada

        self._com_chave()
        mock_client_class.return_value.criar_cobranca.side_effect = cobranca_criada
        self._logar(self.responsavel)
        url = f"/responsavel/mensalidade/{self.mensalidade.pk}/pagar/"

        self.client.get(url)
        self.client.get(url)

        mock_client_class.return_value.criar_cobranca.assert_called_once()

    @patch("integracoes.woovi.services.WooviClient")
    def test_pagar_mensalidade_vencida_tambem_gera_pix(self, mock_client_class):
        # Regressão: a integração anterior só cobrava mensalidades
        # "pendente" — justamente as atrasadas ficavam sem como pagar.
        from tests.woovi_base import cobranca_criada

        self._com_chave()
        mock_client_class.return_value.criar_cobranca.side_effect = cobranca_criada
        self.mensalidade.status = "vencida"
        self.mensalidade.save(update_fields=["status"])
        self._logar(self.responsavel)

        resposta = self.client.get(f"/responsavel/mensalidade/{self.mensalidade.pk}/pagar/")

        self.assertContains(resposta, "00020126PIX")

    @patch("integracoes.woovi.services.WooviClient")
    def test_falha_ao_gerar_pix_mostra_mensagem_generica(self, mock_client_class):
        from integracoes.woovi.exceptions import WooviUnavailableError

        self._com_chave()
        mock_client_class.return_value.criar_cobranca.side_effect = WooviUnavailableError(
            "A Woovi retornou o status HTTP 503."
        )
        self._logar(self.responsavel)

        with self.assertLogs("portal.views_responsavel", level="WARNING"):
            resposta = self.client.get(f"/responsavel/mensalidade/{self.mensalidade.pk}/pagar/")

        self.assertContains(resposta, "Não foi possível carregar o Pix agora")
        self.assertNotContains(resposta, "Woovi")
        self.assertNotContains(resposta, "503")

    def test_pagar_mensalidade_ja_paga_mostra_aviso_sem_chamar_o_provedor(self):
        self.mensalidade.status = "paga"
        self.mensalidade.pago_em = timezone.now()
        self.mensalidade.save(update_fields=["status", "pago_em"])
        self._logar(self.responsavel)
        with patch("integracoes.woovi.services.WooviClient") as mock_client_class:
            resposta = self.client.get(f"/responsavel/mensalidade/{self.mensalidade.pk}/pagar/")
        self.assertContains(resposta, "já está paga")
        mock_client_class.assert_not_called()

    def test_sair_encerra_sessao(self):
        self._logar(self.responsavel)
        self.client.post("/responsavel/sair/")
        self.assertNotIn("responsavel_id", self.client.session)


class GerarAcessoResponsavelTests(TestCase):
    def setUp(self):
        self.a = Academia.objects.create(nome="A", cnpj="GAR1")
        self.user = get_user_model().objects.create_user("gestor", password="senha123")
        AcessoAcademia.objects.create(usuario=self.user, academia=self.a)
        self.responsavel = Responsavel.objects.create(
            academia=self.a, nome="Maria", cpf="1", whatsapp="21999999999"
        )
        self.atleta = Atleta.objects.create(
            academia=self.a, nome="Filho", responsavel_financeiro=self.responsavel
        )
        self.client.force_login(self.user)

    def test_sem_whatsapp_configurado_mostra_link_manual(self):
        resposta = self.client.post(f"/alunos/{self.atleta.pk}/acesso-responsavel/")
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "/responsavel/entrar/")
        self.assertEqual(TokenAcessoResponsavel.objects.filter(responsavel=self.responsavel).count(), 1)

    @patch("integracoes.whatsapp.services.WhatsAppClient")
    def test_com_whatsapp_configurado_envia_e_redireciona(self, mock_client_class):
        mock_client_class.return_value.enviar_template.return_value = {"ok": True}
        resposta = self.client.post(f"/alunos/{self.atleta.pk}/acesso-responsavel/", follow=True)
        self.assertContains(resposta, "enviado automaticamente")
        mock_client_class.return_value.enviar_template.assert_called_once()

    def test_aluno_sem_responsavel_mostra_erro(self):
        atleta_sem_resp = Atleta.objects.create(academia=self.a, nome="Sem responsável")
        resposta = self.client.post(f"/alunos/{atleta_sem_resp.pk}/acesso-responsavel/", follow=True)
        self.assertContains(resposta, "não tem responsável financeiro")
