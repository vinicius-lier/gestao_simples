from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from academias.models import Academia
from atletas.models import Atleta, Responsavel
from financeiro.models import CobrancaPix, ContaRecebimento, Mensalidade
from financeiro.recibo import valor_por_extenso
from matriculas.models import Matricula
from modalidades.models import Modalidade
from portal.models import AcessoAcademia, FichaMatricula, TokenAcessoResponsavel

SENHA = 'judo-forte-2026'


class Cenario(TestCase):
    def setUp(self):
        self.academia = Academia.objects.create(nome='Keiko', cnpj='K1')
        self.modalidade = Modalidade.objects.create(academia=self.academia, nome='Judô')
        self.responsavel = Responsavel.objects.create(
            academia=self.academia, nome='Maria Souza', cpf='529.982.247-25', whatsapp='(21) 99999-8888',
        )
        self.aluno = Atleta.objects.create(academia=self.academia, nome='Ana Souza', responsavel_financeiro=self.responsavel)
        self.matricula = Matricula.objects.create(
            academia=self.academia, atleta=self.aluno, modalidade=self.modalidade,
            valor_mensalidade=Decimal('150.00'), dia_vencimento=10, data_inicio=date(2026, 1, 1),
        )

    def cobranca(self, status='pendente', competencia=date(2026, 9, 1), **kw):
        return Mensalidade.objects.create(
            academia=self.academia, matricula=kw.pop('matricula', self.matricula), competencia=competencia,
            valor=Decimal('150.00'), vencimento=competencia.replace(day=10), status=status, **kw,
        )

    def entrar_pelo_link(self, responsavel=None, proximo=''):
        acesso = TokenAcessoResponsavel.gerar(responsavel or self.responsavel)
        return self.client.post(f'/responsavel/entrar/{acesso.token}/{proximo}')

    def login(self, identificacao, senha=SENHA):
        return self.client.post('/responsavel/entrar/', {'identificacao': identificacao, 'senha': senha})


class LoginComSenhaTests(Cenario):
    def test_primeiro_acesso_pelo_link_leva_a_criar_a_senha(self):
        resp = self.entrar_pelo_link()
        self.assertRedirects(resp, '/responsavel/senha/')
        pagina = self.client.get('/responsavel/senha/')
        self.assertNotContains(pagina, 'Senha atual')

        resp = self.client.post('/responsavel/senha/', {'nova_senha': SENHA, 'confirmacao': SENHA})
        self.assertRedirects(resp, '/responsavel/')
        self.responsavel.refresh_from_db()
        self.assertTrue(self.responsavel.tem_senha)
        self.assertNotEqual(self.responsavel.senha, SENHA)  # só o hash

    def test_entra_com_cpf_ou_whatsapp_e_senha(self):
        self.responsavel.definir_senha(SENHA)
        for identificacao in ('52998224725', '529.982.247-25', '(21) 99999-8888', '5521999998888'):
            with self.subTest(identificacao=identificacao):
                self.client.logout()
                self.assertRedirects(self.login(identificacao), '/responsavel/')
                self.assertContains(self.client.get('/responsavel/'), 'Ana Souza')

    def test_senha_errada_nao_entra_e_bloqueia_depois_de_varias(self):
        self.responsavel.definir_senha(SENHA)
        for _ in range(Responsavel.TENTATIVAS_ANTES_DO_BLOQUEIO):
            self.assertContains(self.login('52998224725', 'errada-123'), 'CPF/WhatsApp ou senha incorretos')
        resp = self.login('52998224725')  # a certa, mas bloqueado
        self.assertContains(resp, 'Muitas tentativas')
        self.assertNotIn('responsavel_id', self.client.session)

        Responsavel.objects.filter(pk=self.responsavel.pk).update(bloqueado_ate=timezone.now() - timedelta(seconds=1))
        self.assertRedirects(self.login('52998224725'), '/responsavel/')

    def test_sem_senha_criada_nao_entra(self):
        self.assertContains(self.login('52998224725'), 'CPF/WhatsApp ou senha incorretos')

    def test_portal_sem_sessao_manda_para_o_login_e_volta(self):
        self.responsavel.definir_senha(SENHA)
        self.assertRedirects(self.client.get('/responsavel/'), '/responsavel/entrar/?next=%2Fresponsavel%2F')
        resp = self.client.post('/responsavel/entrar/?next=/responsavel/senha/', {'identificacao': '52998224725', 'senha': SENHA})
        self.assertRedirects(resp, '/responsavel/senha/')

    def test_login_nao_redireciona_para_fora_do_portal(self):
        self.responsavel.definir_senha(SENHA)
        resp = self.client.post('/responsavel/entrar/?next=//evil.com/', {'identificacao': '52998224725', 'senha': SENHA})
        self.assertRedirects(resp, '/responsavel/')

    def test_trocar_senha_logado_com_senha_pede_a_atual(self):
        self.responsavel.definir_senha(SENHA)
        self.login('52998224725')
        resp = self.client.post('/responsavel/senha/', {'senha_atual': 'errada', 'nova_senha': 'outra-senha-99', 'confirmacao': 'outra-senha-99'})
        self.assertContains(resp, 'Senha atual incorreta')
        self.client.post('/responsavel/senha/', {'senha_atual': SENHA, 'nova_senha': 'outra-senha-99', 'confirmacao': 'outra-senha-99'})
        self.responsavel.refresh_from_db()
        self.assertTrue(self.responsavel.conferir_senha('outra-senha-99'))

    def test_senha_fraca_ou_diferente_e_recusada(self):
        self.entrar_pelo_link()
        fraca = self.client.post('/responsavel/senha/', {'nova_senha': '12345678', 'confirmacao': '12345678'})
        self.assertIn('nova_senha', fraca.context['form'].errors)
        self.assertContains(self.client.post('/responsavel/senha/', {'nova_senha': SENHA, 'confirmacao': SENHA + 'x'}), 'não conferem')
        self.responsavel.refresh_from_db()
        self.assertFalse(self.responsavel.tem_senha)

    def test_sair_volta_para_o_login(self):
        self.responsavel.definir_senha(SENHA)
        self.login('52998224725')
        self.assertRedirects(self.client.post('/responsavel/sair/'), '/responsavel/entrar/')
        self.assertEqual(self.client.get('/responsavel/').status_code, 302)


class EsqueciSenhaTests(Cenario):
    @patch('integracoes.whatsapp.enviar_acesso')
    def test_manda_link_que_leva_a_criar_a_senha(self, enviar):
        resp = self.client.post('/responsavel/esqueci-a-senha/', {'identificacao': '(21) 99999-8888'})
        self.assertContains(resp, 'Confira seu WhatsApp')
        _academia, responsavel, link = enviar.call_args.args
        self.assertEqual(responsavel, self.responsavel)
        self.assertIn('next=%2Fresponsavel%2Fsenha%2F', link)

        # Um segundo pedido logo em seguida não manda outra mensagem.
        self.client.post('/responsavel/esqueci-a-senha/', {'identificacao': '52998224725'})
        enviar.assert_called_once()

        # O link entra e vai direto para a senha, sem pedir a atual.
        caminho = link.split('testserver', 1)[1]
        self.assertRedirects(self.client.post(caminho), '/responsavel/senha/')
        self.assertNotContains(self.client.get('/responsavel/senha/'), 'Senha atual')

    @patch('integracoes.whatsapp.enviar_acesso')
    def test_cadastro_desconhecido_tem_a_mesma_resposta(self, enviar):
        resp = self.client.post('/responsavel/esqueci-a-senha/', {'identificacao': '11122233344'})
        self.assertContains(resp, 'Confira seu WhatsApp')
        enviar.assert_not_called()


class ReciboTests(Cenario):
    def paga(self):
        return self.cobranca(status='paga', pago_em=timezone.now(), forma_pagamento='pix', valor_pago=Decimal('170.00'))

    def test_familia_baixa_o_recibo_da_cobranca_paga(self):
        paga = self.paga()
        self.entrar_pelo_link()
        self.assertContains(self.client.get('/responsavel/'), f'/responsavel/mensalidade/{paga.pk}/recibo/')
        resp = self.client.get(f'/responsavel/mensalidade/{paga.pk}/recibo/')
        self.assertEqual(resp['Content-Type'], 'application/pdf')
        self.assertIn('attachment; filename="recibo-ana-souza-2026-09.pdf"', resp['Content-Disposition'])
        self.assertTrue(resp.content.startswith(b'%PDF'))

    def test_sem_recibo_de_cobranca_aberta_ou_de_outra_familia(self):
        aberta = self.cobranca(competencia=date(2026, 10, 1))
        outro = Responsavel.objects.create(academia=self.academia, nome='Outro', whatsapp='21988887777')
        outro_aluno = Atleta.objects.create(academia=self.academia, nome='Beto', responsavel_financeiro=outro)
        outra_matricula = Matricula.objects.create(
            academia=self.academia, atleta=outro_aluno, modalidade=self.modalidade,
            valor_mensalidade=Decimal('150.00'), data_inicio=date(2026, 1, 1),
        )
        alheia = self.cobranca(status='paga', matricula=outra_matricula, pago_em=timezone.now())
        self.entrar_pelo_link()
        self.assertEqual(self.client.get(f'/responsavel/mensalidade/{aberta.pk}/recibo/').status_code, 404)
        self.assertEqual(self.client.get(f'/responsavel/mensalidade/{alheia.pk}/recibo/').status_code, 404)

    def test_escola_baixa_o_recibo_pelo_painel(self):
        paga = self.paga()
        usuario = get_user_model().objects.create_user('prof', password='x')
        AcessoAcademia.objects.create(usuario=usuario, academia=self.academia)
        self.client.force_login(usuario)
        self.assertContains(self.client.get(f'/alunos/{self.aluno.pk}/'), f'/financeiro/cobrancas/{paga.pk}/recibo/')
        resp = self.client.get(f'/financeiro/cobrancas/{paga.pk}/recibo/')
        self.assertTrue(resp.content.startswith(b'%PDF'))

    def test_valor_por_extenso(self):
        self.assertEqual(valor_por_extenso(Decimal('170.50')), 'cento e setenta reais e cinquenta centavos')
        self.assertEqual(valor_por_extenso(Decimal('1001')), 'mil e um reais')
        self.assertEqual(valor_por_extenso(Decimal('1')), 'um real')


class ExcluirAlunoTests(Cenario):
    def setUp(self):
        super().setUp()
        self.admin = get_user_model().objects.create_user('dona', password='x')
        AcessoAcademia.objects.create(usuario=self.admin, academia=self.academia, administrador=True)
        self.client.force_login(self.admin)

    def test_sem_pagamento_apaga_tudo_e_o_responsavel_sem_outro_aluno(self):
        self.cobranca()
        FichaMatricula.objects.create(
            academia=self.academia, atleta=self.aluno, origem=FichaMatricula.PLANILHA,
            respondida_em=timezone.now(), nome_aluno='Ana Souza',
        )
        self.assertContains(self.client.get(f'/alunos/{self.aluno.pk}/excluir/'), 'Excluir de vez')
        self.assertRedirects(self.client.post(f'/alunos/{self.aluno.pk}/excluir/', {'acao': 'excluir'}), '/alunos/')
        self.assertFalse(Atleta.objects.exists() or Matricula.objects.exists() or Mensalidade.objects.exists())
        self.assertFalse(FichaMatricula.objects.exists() or Responsavel.objects.exists())

    def test_responsavel_com_outro_aluno_continua(self):
        Atleta.objects.create(academia=self.academia, nome='Irmão', responsavel_financeiro=self.responsavel)
        self.client.post(f'/alunos/{self.aluno.pk}/excluir/', {'acao': 'excluir'})
        self.assertTrue(Responsavel.objects.filter(pk=self.responsavel.pk).exists())

    def test_com_pagamento_ou_pix_nao_apaga_e_oferece_inativar(self):
        self.cobranca(status='paga', pago_em=timezone.now())
        pagina = self.client.get(f'/alunos/{self.aluno.pk}/excluir/')
        self.assertContains(pagina, 'não pode ser excluído')
        self.client.post(f'/alunos/{self.aluno.pk}/excluir/', {'acao': 'excluir'})
        self.assertTrue(Atleta.objects.filter(pk=self.aluno.pk).exists())

        self.assertRedirects(self.client.post(f'/alunos/{self.aluno.pk}/excluir/', {'acao': 'inativar'}), f'/alunos/{self.aluno.pk}/')
        self.aluno.refresh_from_db()
        self.assertEqual(self.aluno.status, 'inativo')

    def test_pix_gerado_tambem_bloqueia(self):
        aberta = self.cobranca()
        conta = ContaRecebimento.objects.create(academia=self.academia, tipo_chave=ContaRecebimento.EMAIL, pix_key='a@b.com')
        CobrancaPix.objects.create(
            mensalidade=aberta, conta_recebimento=conta, correlation_id='c1', valor=Decimal('150'),
            br_code='x', expira_em=timezone.now() + timedelta(days=1),
        )
        self.client.post(f'/alunos/{self.aluno.pk}/excluir/', {'acao': 'excluir'})
        self.assertTrue(Atleta.objects.filter(pk=self.aluno.pk).exists())

    def test_so_administrador(self):
        prof = get_user_model().objects.create_user('prof', password='x')
        AcessoAcademia.objects.create(usuario=prof, academia=self.academia)
        self.client.force_login(prof)
        self.assertEqual(self.client.get(f'/alunos/{self.aluno.pk}/excluir/').status_code, 403)
        self.assertEqual(self.client.post(f'/alunos/{self.aluno.pk}/excluir/', {'acao': 'excluir'}).status_code, 403)
        self.assertNotContains(self.client.get(f'/alunos/{self.aluno.pk}/'), 'Excluir aluno')
        self.assertTrue(Atleta.objects.exists())
