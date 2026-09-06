from django.test import TestCase
from django.contrib.auth import get_user_model
from academias.models import Academia, Unidade
from atletas.models import Atleta, Responsavel
from modalidades.models import Modalidade, Professor, Turma
from portal.models import AcessoAcademia


class NovosCadastrosTests(TestCase):
    def setUp(self):
        self.a = Academia.objects.create(nome='A', cnpj='A')
        self.b = Academia.objects.create(nome='B', cnpj='B')
        self.user = get_user_model().objects.create_user('gestor', password='senha123')
        self.acesso = AcessoAcademia.objects.create(usuario=self.user, academia=self.a, administrador=True)
        self.unit = Unidade.objects.create(academia=self.a, nome='Matriz')
        self.unit2 = Unidade.objects.create(academia=self.a, nome='Polo 2')
        self.s = Modalidade.objects.create(academia=self.a, nome='Judô')
        self.p = Professor.objects.create(academia=self.a, nome='Professora')
        self.client.force_login(self.user)

    def test_cadastro_unidade_professor_turma(self):
        self.assertEqual(self.client.post('/unidades/novo/', {'nome':'Polo 3', 'ativo':'on'}).status_code, 302)
        self.assertEqual(self.client.post('/professores/novo/', {'nome':'Professor 2', 'ativo':'on'}).status_code, 302)
        response = self.client.post('/turmas/novo/', {'nome':'Infantil', 'unidade':self.unit.pk, 'modalidade':self.s.pk, 'docente':self.p.pk, 'ativo':'on'})
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

    def test_excluir_cadastro(self):
        prof = Professor.objects.create(academia=self.a, nome='Temporário')
        alheio = Professor.objects.create(academia=self.b, nome='De outra')
        self.assertEqual(self.client.get(f'/professores/{prof.pk}/excluir/').status_code, 302)  # GET só redireciona
        self.assertTrue(Professor.objects.filter(pk=prof.pk).exists())
        self.assertEqual(self.client.post(f'/professores/{prof.pk}/excluir/').status_code, 302)
        self.assertFalse(Professor.objects.filter(pk=prof.pk).exists())
        self.assertEqual(self.client.post(f'/professores/{alheio.pk}/excluir/').status_code, 404)
        self.acesso.administrador = False
        self.acesso.save()
        p2 = Professor.objects.create(academia=self.a, nome='Outro')
        self.assertEqual(self.client.post(f'/professores/{p2.pk}/excluir/').status_code, 403)
        self.assertTrue(Professor.objects.filter(pk=p2.pk).exists())

    def test_excluir_bloqueado_por_vinculo(self):
        Turma.objects.create(academia=self.a, modalidade=self.s, unidade=self.unit, nome='T')
        resp = self.client.post(f'/modalidades/{self.s.pk}/excluir/', follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(Modalidade.objects.filter(pk=self.s.pk).exists())
        self.assertContains(resp, 'não pode ser excluído')

    def test_turma_rejeita_relacao_outra_academia(self):
        p = Professor.objects.create(academia=self.b, nome='Secreto')
        response = self.client.post('/turmas/novo/', {'nome':'Teste', 'unidade':self.unit.pk,'modalidade':self.s.pk,'docente':p.pk,'ativo':'on'})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Turma.objects.exists())
        self.assertNotContains(self.client.get('/professores/'), 'Secreto')
        self.assertEqual(self.client.get(f'/professores/{p.pk}/editar/').status_code, 404)

    def payload(self):
        return {'nome':'Aluno adulto','cpf':'12345678900','status':'ativo','proprio_responsavel':'on','aluno_whatsapp':'21999999999','aluno_email':'aluno@example.com','matricula-unidade':self.unit.pk,'matricula-modalidade':self.s.pk,'matricula-valor_mensalidade':'150','matricula-dia_vencimento':'10','matricula-data_inicio':'2026-09-06','matricula-ativo':'on'}

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
        turma=Turma.objects.create(academia=self.a,modalidade=self.s,unidade=self.unit2,docente=self.p,nome='Outra')
        data=self.payload();data['matricula-turma']=turma.pk
        self.assertEqual(self.client.post('/alunos/novo/',data).status_code,200)
        self.assertFalse(Atleta.objects.exists())

    def test_matricula_puxa_valor_e_vencimento_da_turma(self):
        turma=Turma.objects.create(academia=self.a,modalidade=self.s,unidade=self.unit,nome='T1',valor_mensalidade='222.00',dia_vencimento=7)
        data=self.payload();data['matricula-turma']=turma.pk
        data['matricula-valor_mensalidade']='';data['matricula-dia_vencimento']=''
        self.assertEqual(self.client.post('/alunos/novo/',data).status_code,302)
        m=Atleta.objects.get().matriculas.get()
        self.assertEqual((str(m.valor_mensalidade),m.dia_vencimento),('222.00',7))


    def faixas(self, *linhas, total=None, initial=0):
        base = {'faixa-TOTAL_FORMS': str(total if total is not None else max(len(linhas), 1)),
                'faixa-INITIAL_FORMS': str(initial), 'faixa-MIN_NUM_FORMS': '0', 'faixa-MAX_NUM_FORMS': '1000'}
        for i, linha in enumerate(linhas):
            for campo, valor in linha.items():
                base[f'faixa-{i}-{campo}'] = valor
        return base

    def test_modalidade_so_tem_nome_sem_valores(self):
        data = {'nome': 'Judô adulto', 'descricao': 'Aulas regulares', 'ativo': 'on', **self.faixas()}
        self.assertEqual(self.client.post('/modalidades/novo/', data).status_code, 302)
        modalidade = Modalidade.objects.get(nome='Judô adulto')
        self.assertEqual(modalidade.academia, self.a)
        self.assertFalse(hasattr(modalidade, 'valor_padrao'))
        self.assertContains(self.client.get('/modalidades/'), 'Judô adulto')
        data['nome'] = 'Judô kids'
        self.assertEqual(self.client.post(f'/modalidades/{modalidade.pk}/editar/', data).status_code, 302)
        modalidade.refresh_from_db()
        self.assertEqual(modalidade.nome, 'Judô kids')
        outro = Modalidade.objects.create(academia=self.b, nome='Modalidade secreta')
        self.assertNotContains(self.client.get('/modalidades/'), 'Modalidade secreta')
        self.assertEqual(self.client.get(f'/modalidades/{outro.pk}/editar/').status_code, 404)
        self.acesso.administrador = False
        self.acesso.save()
        self.assertEqual(self.client.post('/modalidades/novo/', data).status_code, 403)

    def test_graduacoes_inline_na_modalidade(self):
        from modalidades.models import Graduacao
        criar = {'nome': 'Judô', 'ativo': 'on', **self.faixas({'nome': 'Azul', 'ordem': '2', 'ativo': 'on'})}
        self.assertEqual(self.client.post(f'/modalidades/{self.s.pk}/editar/', criar).status_code, 302)
        faixa = Graduacao.objects.get(nome='Azul')
        self.assertEqual((faixa.academia_id, faixa.modalidade_id), (self.a.pk, self.s.pk))
        self.assertContains(self.client.get('/modalidades/'), '1 faixa')
        editar = {'nome': 'Judô', 'ativo': 'on',
                  **self.faixas({'id': faixa.pk, 'nome': 'Azul', 'ordem': '5', 'ativo': 'on'}, initial=1)}
        self.assertEqual(self.client.post(f'/modalidades/{self.s.pk}/editar/', editar).status_code, 302)
        faixa.refresh_from_db()
        self.assertEqual(faixa.ordem, 5)
        remover = {'nome': 'Judô', 'ativo': 'on',
                   **self.faixas({'id': faixa.pk, 'nome': 'Azul', 'ordem': '5', 'DELETE': 'on'}, initial=1)}
        self.assertEqual(self.client.post(f'/modalidades/{self.s.pk}/editar/', remover).status_code, 302)
        self.assertFalse(Graduacao.objects.filter(pk=faixa.pk).exists())

    def test_professor_faixa_judo_e_graduacao_legada(self):
        from modalidades.models import Graduacao
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

    def test_professor_nao_aceita_faixa_de_outra_academia(self):
        from modalidades.models import Graduacao
        outra = Modalidade.objects.create(academia=self.b, nome='Outra luta')
        estrangeira = Graduacao.objects.create(academia=self.b, modalidade=outra, nome='Secreta')
        self.assertEqual(self.client.post('/professores/novo/', {'nome': 'Inválido', 'faixa': estrangeira.pk}).status_code, 200)
        self.assertFalse(Professor.objects.filter(nome='Inválido').exists())

    def test_turma_guarda_valor_e_horario(self):
        data = {'nome': 'Adulto noite', 'unidade': self.unit.pk, 'modalidade': self.s.pk, 'docente': self.p.pk,
                'horario': '19:30', 'dias_semana': 'Seg/Qua', 'valor_mensalidade': '180.00', 'dia_vencimento': '5', 'ativo': 'on'}
        self.assertEqual(self.client.post('/turmas/novo/', data).status_code, 302)
        turma = Turma.objects.get(nome='Adulto noite')
        self.assertEqual((str(turma.valor_mensalidade), turma.dia_vencimento), ('180.00', 5))
        self.assertEqual(self.client.post('/turmas/novo/', {**data, 'nome': 'X', 'valor_mensalidade': '-1'}).status_code, 200)
        self.assertEqual(self.client.post('/turmas/novo/', {**data, 'nome': 'Y', 'dia_vencimento': '40'}).status_code, 200)
