from django.test import TestCase
from django.contrib.auth import get_user_model
from academias.models import Academia, Unidade
from atletas.models import Atleta, Responsavel
from servicos.models import Servico, Professor, Turma
from portal.models import AcessoAcademia


class NovosCadastrosTests(TestCase):
    def setUp(self):
        self.a = Academia.objects.create(nome='A', cnpj='A')
        self.b = Academia.objects.create(nome='B', cnpj='B')
        self.user = get_user_model().objects.create_user('gestor', password='senha123')
        self.acesso = AcessoAcademia.objects.create(usuario=self.user, academia=self.a, administrador=True)
        self.unit = Unidade.objects.create(academia=self.a, nome='Matriz')
        self.unit2 = Unidade.objects.create(academia=self.a, nome='Polo 2')
        self.s = Servico.objects.create(academia=self.a, nome='Judô', valor_padrao=150)
        self.p = Professor.objects.create(academia=self.a, nome='Professora')
        self.client.force_login(self.user)

    def test_cadastro_unidade_professor_turma(self):
        self.assertEqual(self.client.post('/unidades/novo/', {'nome':'Polo 3', 'ativo':'on'}).status_code, 302)
        self.assertEqual(self.client.post('/professores/novo/', {'nome':'Professor 2', 'ativo':'on'}).status_code, 302)
        response = self.client.post('/turmas/novo/', {'nome':'Infantil', 'unidade':self.unit.pk, 'servico':self.s.pk, 'docente':self.p.pk, 'ativo':'on'})
        self.assertEqual(response.status_code, 302)
        turma = Turma.objects.get()
        self.assertEqual(turma.academia, self.a)
        self.assertEqual(turma.docente, self.p)
        for path in ['/unidades/', '/professores/', '/turmas/']:
            self.assertEqual(self.client.get(path).status_code, 200)

    def test_nao_admin_nao_cria(self):
        self.acesso.administrador = False
        self.acesso.save()
        for path in ['/unidades/novo/', '/professores/novo/', '/turmas/novo/']:
            self.assertEqual(self.client.get(path).status_code, 403)
            self.assertEqual(self.client.post(path, {'nome':'Bloqueado'}).status_code, 403)

    def test_turma_rejeita_relacao_outra_academia(self):
        p = Professor.objects.create(academia=self.b, nome='Secreto')
        response = self.client.post('/turmas/novo/', {'nome':'Teste', 'unidade':self.unit.pk,'servico':self.s.pk,'docente':p.pk,'ativo':'on'})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Turma.objects.exists())
        self.assertNotContains(self.client.get('/professores/'), 'Secreto')
        self.assertEqual(self.client.get(f'/professores/{p.pk}/editar/').status_code, 404)

    def payload(self):
        return {'nome':'Aluno adulto','cpf':'12345678900','status':'ativo','proprio_responsavel':'on','aluno_whatsapp':'21999999999','aluno_email':'aluno@example.com','matricula-unidade':self.unit.pk,'matricula-servico':self.s.pk,'matricula-valor_mensalidade':'150','matricula-dia_vencimento':'10','matricula-data_inicio':'2026-09-06','matricula-ativo':'on'}

    def test_aluno_responsavel_financeiro_reutiliza_asaas(self):
        r = Responsavel.objects.create(academia=self.a,nome='Aluno adulto',cpf='123.456.789-00',whatsapp='21999999999',asaas_customer_id='cus_existente')
        self.assertEqual(self.client.post('/alunos/novo/',self.payload()).status_code, 302)
        aluno = Atleta.objects.get()
        self.assertTrue(aluno.proprio_responsavel)
        self.assertEqual(aluno.responsavel_financeiro_id,r.pk)
        self.assertEqual(aluno.responsavel_financeiro.asaas_customer_id,'cus_existente')
        self.assertContains(self.client.get(f'/alunos/{aluno.pk}/'), 'Próprio aluno')

    def test_proprio_responsavel_exige_contato_e_cpf(self):
        data=self.payload();data['cpf']='';data['aluno_whatsapp']=''
        self.assertEqual(self.client.post('/alunos/novo/',data).status_code,200)
        self.assertFalse(Atleta.objects.exists())
        self.assertFalse(Responsavel.objects.exists())

    def test_matricula_outro_polo_so_admin(self):
        self.client.post('/alunos/novo/',self.payload())
        aluno=Atleta.objects.get()
        data=self.payload();data['matricula-unidade']=self.unit2.pk
        path=f'/alunos/{aluno.pk}/matriculas/nova/'
        self.acesso.administrador=False;self.acesso.save()
        self.assertEqual(self.client.post(path,data).status_code,403)
        self.acesso.administrador=True;self.acesso.save()
        self.assertEqual(self.client.post(path,data).status_code,302)
        self.assertEqual(aluno.matriculas.count(),2)
        self.assertEqual(Atleta.objects.count(),1)

    def test_nao_admin_nao_transfere_polo(self):
        self.client.post('/alunos/novo/',self.payload())
        aluno=Atleta.objects.get();m=aluno.matriculas.get()
        self.acesso.administrador=False;self.acesso.save()
        data=self.payload();data['matricula-unidade']=self.unit2.pk
        self.assertEqual(self.client.post(f'/alunos/{aluno.pk}/matriculas/{m.pk}/editar/',data).status_code,200)
        m.refresh_from_db();self.assertEqual(m.unidade_id,self.unit.pk)

    def test_turma_polo_incompativel(self):
        turma=Turma.objects.create(academia=self.a,servico=self.s,unidade=self.unit2,docente=self.p,nome='Outra')
        data=self.payload();data['matricula-turma']=turma.pk
        self.assertEqual(self.client.post('/alunos/novo/',data).status_code,200)
        self.assertFalse(Atleta.objects.exists())


    def test_servico_criar_editar_validar_e_isolar(self):
        data={'nome':'Judô adulto','descricao':'Aulas regulares','valor_padrao':'180.00','dia_vencimento':'10','ativo':'on'}
        self.assertEqual(self.client.post('/servicos/novo/',data).status_code,302)
        servico=Servico.objects.get(nome='Judô adulto')
        self.assertEqual(servico.academia,self.a)
        self.assertContains(self.client.get('/servicos/'),'Judô adulto')
        data['valor_padrao']='200.00'
        self.assertEqual(self.client.post(f'/servicos/{servico.pk}/editar/',data).status_code,302)
        servico.refresh_from_db();self.assertEqual(servico.valor_padrao,200)
        for changes in [{'dia_vencimento':'32'},{'valor_padrao':'-1'}]:
            self.assertEqual(self.client.post('/servicos/novo/',{**data,**changes}).status_code,200)
        outro=Servico.objects.create(academia=self.b,nome='Serviço secreto',valor_padrao=100)
        self.assertNotContains(self.client.get('/servicos/'),'Serviço secreto')
        self.assertEqual(self.client.get(f'/servicos/{outro.pk}/editar/').status_code,404)
        self.acesso.administrador=False;self.acesso.save()
        self.assertEqual(self.client.post('/servicos/novo/',data).status_code,403)


    def test_professor_faixa_judo_e_graduacao_legada(self):
        from servicos.models import Graduacao
        faixa = Graduacao.objects.create(academia=self.a, modalidade=self.s, nome='Preta — 3º dan')
        response=self.client.post('/professores/novo/', {'nome':'Sensei','faixa':faixa.pk,'ativo':'on'})
        self.assertEqual(response.status_code,302)
        professor=Professor.objects.get(nome='Sensei')
        self.assertEqual(professor.faixa,faixa)
        self.assertContains(self.client.get('/professores/'),'Preta — 3º dan')
        self.assertEqual(self.client.post('/professores/novo/',{'nome':'Inválido','graduacao':'inventada'}).status_code,200)
        professor.graduacao='Faixa preta terceiro dan';professor.save()
        response=self.client.post(f'/professores/{professor.pk}/editar/',{'nome':'Sensei atualizado','graduacao':'Faixa preta terceiro dan','ativo':'on'})
        self.assertEqual(response.status_code,302)
        professor.refresh_from_db();self.assertEqual(professor.graduacao,'Faixa preta terceiro dan')

    def test_graduacoes_por_modalidade_e_isolamento(self):
        from servicos.models import Graduacao
        data = {'modalidade': self.s.pk, 'nome': 'Azul', 'ordem': 2, 'ativo': 'on'}
        self.assertEqual(self.client.post('/graduacoes/novo/', data).status_code, 302)
        faixa = Graduacao.objects.get(nome='Azul')
        self.assertContains(self.client.get('/graduacoes/'), 'Azul')
        self.assertContains(self.client.get('/modalidades/'), 'Judô')
        self.assertEqual(self.client.post('/graduacoes/novo/', data).status_code, 200)
        outra = Servico.objects.create(academia=self.b, nome='Outra luta', valor_padrao=100)
        self.assertEqual(self.client.post('/graduacoes/novo/', {**data, 'modalidade': outra.pk}).status_code, 200)
        estrangeira = Graduacao.objects.create(academia=self.b, modalidade=outra, nome='Secreta')
        self.assertEqual(self.client.get(f'/graduacoes/{estrangeira.pk}/editar/').status_code, 404)
        self.assertEqual(self.client.post('/professores/novo/', {'nome': 'Inválido', 'faixa': estrangeira.pk}).status_code, 200)
        self.assertFalse(Professor.objects.filter(nome='Inválido').exists())
        self.assertEqual(self.client.post(f'/graduacoes/{faixa.pk}/editar/', {**data, 'ordem': 3}).status_code, 302)
        faixa.refresh_from_db()
        self.assertEqual(faixa.ordem, 3)
        self.acesso.administrador = False
        self.acesso.save()
        self.assertEqual(self.client.post('/graduacoes/novo/', data).status_code, 403)
