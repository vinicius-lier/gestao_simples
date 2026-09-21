from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from academias.models import Academia, Unidade
from atletas.models import Atleta, Responsavel
from matriculas.models import Matricula
from modalidades.models import Modalidade, Turma
from portal.models import AcessoAcademia, ConviteMatricula


class BaseConvite(TestCase):
    def setUp(self):
        User = get_user_model()
        self.a = Academia.objects.create(nome='A', cnpj='A')
        self.b = Academia.objects.create(nome='B', cnpj='B')
        self.prof = User.objects.create_user('prof', password='x')
        AcessoAcademia.objects.create(usuario=self.prof, academia=self.a, administrador=False)
        self.outro = User.objects.create_user('outro', password='x')
        AcessoAcademia.objects.create(usuario=self.outro, academia=self.b, administrador=True)
        self.unit = Unidade.objects.create(academia=self.a, nome='Matriz')
        self.mod = Modalidade.objects.create(academia=self.a, nome='Judô')
        self.turma = Turma.objects.create(
            academia=self.a, modalidade=self.mod, unidade=self.unit, nome='Infantil',
            valor_mensalidade=Decimal('150.00'), dia_vencimento=10,
        )

    def convite(self, **kw):
        base = dict(academia=self.a, modalidade=self.mod, turma=self.turma, unidade=self.unit, criado_por=self.prof)
        base.update(kw)
        return ConviteMatricula.gerar(**base)

    def dados_familia(self, **kw):
        base = dict(nome='Aluno Teste', responsavel_nome='Mãe Teste', responsavel_whatsapp='21999998888')
        base.update(kw)
        return base


class GeracaoNoPainel(BaseConvite):
    def test_professor_nao_admin_gera_link(self):
        self.client.force_login(self.prof)
        resp = self.client.post('/matriculas/convites/', {
            'unidade': self.unit.pk, 'modalidade': self.mod.pk, 'turma': self.turma.pk,
            'convidado_nome': 'João', 'validade_dias': 7,
        })
        self.assertEqual(resp.status_code, 302)
        c = ConviteMatricula.objects.get()
        self.assertEqual((c.status, c.criado_por), (ConviteMatricula.PENDENTE, self.prof))
        self.assertTrue(c.token and c.expira_em > timezone.now())

    def test_turma_sem_valor_exige_valor_no_link(self):
        turma2 = Turma.objects.create(academia=self.a, modalidade=self.mod, unidade=self.unit, nome='S/ valor')
        self.client.force_login(self.prof)
        comum = {'unidade': self.unit.pk, 'modalidade': self.mod.pk, 'turma': turma2.pk, 'validade_dias': 7}
        self.assertEqual(self.client.post('/matriculas/convites/', comum).status_code, 200)
        self.assertEqual(ConviteMatricula.objects.count(), 0)
        self.assertEqual(self.client.post('/matriculas/convites/', {**comum, 'valor_mensalidade': '200'}).status_code, 302)
        self.assertEqual(ConviteMatricula.objects.count(), 1)

    def test_exige_login(self):
        self.assertEqual(self.client.get('/matriculas/convites/').status_code, 302)

    def test_isolamento_de_tenant(self):
        c = self.convite()
        self.client.force_login(self.outro)
        self.assertEqual(self.client.get(f'/matriculas/convites/{c.pk}/').status_code, 404)
        self.assertEqual(self.client.post(f'/matriculas/convites/{c.pk}/acao/', {'acao': 'cancelar'}).status_code, 404)


class FormularioPublico(BaseConvite):
    def test_mostra_formulario_com_a_turma(self):
        c = self.convite()
        resp = self.client.get(f'/matricula/{c.token}/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Ficha de matrícula')
        self.assertContains(resp, 'Infantil')

    def test_familia_preenche_e_cria_registros_pendentes(self):
        c = self.convite()
        resp = self.client.post(f'/matricula/{c.token}/', self.dados_familia(
            data_nascimento='2015-05-04', responsavel_cpf='12345678909',
        ), follow=True)
        self.assertEqual(resp.status_code, 200)

        atleta = Atleta.objects.get()
        self.assertEqual(atleta.academia, self.a)
        self.assertEqual(Responsavel.objects.filter(academia=self.a, nome='Mãe Teste').count(), 1)
        mat = Matricula.objects.get()
        self.assertFalse(mat.ativo)
        self.assertEqual((mat.modalidade, mat.turma, mat.valor_mensalidade), (self.mod, self.turma, Decimal('150.00')))

        c.refresh_from_db()
        self.assertEqual(c.status, ConviteMatricula.PREENCHIDO)
        self.assertEqual((c.atleta, c.matricula), (atleta, mat))
        self.assertIsNotNone(c.preenchido_em)

    def test_proprio_responsavel(self):
        c = self.convite()
        self.client.post(f'/matricula/{c.token}/', {
            'nome': 'Adulto Solo', 'cpf': '12345678909',
            'proprio_responsavel': 'on', 'responsavel_whatsapp': '21988887777',
        })
        atleta = Atleta.objects.get()
        self.assertTrue(atleta.proprio_responsavel)
        self.assertEqual(atleta.responsavel_financeiro.nome, 'Adulto Solo')
        self.assertEqual(atleta.responsavel_financeiro.cpf, '12345678909')

    def test_menor_sem_responsavel_e_recusado(self):
        c = self.convite()
        resp = self.client.post(f'/matricula/{c.token}/', {'nome': 'Sem Resp', 'responsavel_whatsapp': '21999998888'})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Atleta.objects.count(), 0)
        self.assertContains(resp, 'nome do responsável')

    def test_link_uso_unico(self):
        c = self.convite()
        self.client.post(f'/matricula/{c.token}/', self.dados_familia())
        self.assertEqual(Atleta.objects.count(), 1)
        resp = self.client.get(f'/matricula/{c.token}/')
        self.assertContains(resp, 'já foram preenchidos')
        self.client.post(f'/matricula/{c.token}/', self.dados_familia(nome='Outro'))
        self.assertEqual(Atleta.objects.count(), 1)

    def test_link_expirado(self):
        c = self.convite()
        ConviteMatricula.objects.filter(pk=c.pk).update(expira_em=timezone.now() - timedelta(days=1))
        resp = self.client.get(f'/matricula/{c.token}/')
        self.assertContains(resp, 'expirou')
        self.client.post(f'/matricula/{c.token}/', self.dados_familia())
        self.assertEqual(Atleta.objects.count(), 0)

    def test_link_invalido(self):
        resp = self.client.get('/matricula/nao-existe/')
        self.assertEqual(resp.status_code, 404)
        self.assertContains(resp, 'Link inválido', status_code=404)


class RevisaoNoPainel(BaseConvite):
    def _preenchido(self):
        c = self.convite()
        self.client.post(f'/matricula/{c.token}/', self.dados_familia())
        c.refresh_from_db()
        return c

    def test_ativar_matricula(self):
        c = self._preenchido()
        self.client.force_login(self.prof)
        resp = self.client.post(f'/matriculas/convites/{c.pk}/acao/', {'acao': 'ativar'})
        self.assertEqual(resp.status_code, 302)
        c.refresh_from_db()
        self.assertEqual(c.status, ConviteMatricula.ATIVADO)
        self.assertTrue(c.matricula.ativo)

    def test_nao_ativa_convite_pendente(self):
        c = self.convite()
        self.client.force_login(self.prof)
        self.client.post(f'/matriculas/convites/{c.pk}/acao/', {'acao': 'ativar'})
        c.refresh_from_db()
        self.assertEqual(c.status, ConviteMatricula.PENDENTE)

    def test_cancelar_convite(self):
        c = self.convite()
        self.client.force_login(self.prof)
        self.client.post(f'/matriculas/convites/{c.pk}/acao/', {'acao': 'cancelar'})
        c.refresh_from_db()
        self.assertEqual(c.status, ConviteMatricula.CANCELADO)
