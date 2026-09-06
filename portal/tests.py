from datetime import date
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.urls import reverse
from academias.models import Academia
from atletas.models import Atleta, Responsavel
from matriculas.models import Matricula
from modalidades.models import Modalidade, Turma
from .models import AcessoAcademia


class PortalTests(TestCase):
    def setUp(self):
        self.a = Academia.objects.create(nome='Academia A', cnpj='1')
        self.b = Academia.objects.create(nome='Academia B', cnpj='2')
        self.user = get_user_model().objects.create_user('professor', password='senha-teste-123')
        AcessoAcademia.objects.create(usuario=self.user, academia=self.a)
        self.s = Modalidade.objects.create(academia=self.a, nome='Judô')
        self.outro = Modalidade.objects.create(academia=self.b, nome='Segredo B')
        self.client.force_login(self.user)
        self.payload = {'nome': 'João', 'status': 'ativo', 'responsavel_nome': 'Maria',
                        'responsavel_cpf': '123.456.789-00', 'responsavel_whatsapp': '21999999999',
                        'matricula-modalidade': self.s.pk, 'matricula-valor_mensalidade': '150.00',
                        'matricula-dia_vencimento': '10', 'matricula-data_inicio': '2026-09-01',
                        'matricula-ativo': 'on'}

    def criar(self):
        response = self.client.post(reverse('portal:novo'), self.payload)
        self.assertEqual(response.status_code, 302)
        return Atleta.objects.get(academia=self.a)

    def test_login_logout_e_redirecionamento(self):
        self.client.logout()
        self.assertRedirects(self.client.get('/painel/'), '/login/?next=/painel/')
        self.assertEqual(self.client.post('/login/', {'username': 'professor', 'password': 'senha-teste-123'}).status_code, 302)
        self.assertEqual(self.client.get('/logout/').status_code, 405)
        self.assertEqual(self.client.post('/logout/').status_code, 302)
        self.assertEqual(self.client.get('/painel/').status_code, 302)

    def test_usuario_sem_academia_inativa_e_superuser(self):
        AcessoAcademia.objects.all().delete()
        self.assertEqual(self.client.get('/painel/').status_code, 403)
        self.user.is_superuser = True
        self.user.save()
        self.assertEqual(self.client.get('/painel/').status_code, 403)
        AcessoAcademia.objects.create(usuario=self.user, academia=self.a)
        self.a.ativo = False
        self.a.save()
        self.assertEqual(self.client.get('/alunos/').status_code, 403)

    def test_cadastro_completo_e_telas(self):
        aluno = self.criar()
        self.assertEqual(aluno.responsavel_financeiro.academia, self.a)
        self.assertEqual(aluno.matriculas.get().academia, self.a)
        for url in ('/painel/', '/alunos/', f'/alunos/{aluno.pk}/', f'/alunos/{aluno.pk}/editar/'):
            self.assertEqual(self.client.get(url).status_code, 200)
        self.assertContains(self.client.get('/painel/'), '1')

    def test_reutiliza_cpf_normalizado_sem_alterar_asaas(self):
        r = Responsavel.objects.create(academia=self.a, nome='Maria existente', cpf='12345678900', whatsapp='21', asaas_customer_id='cus_preservado')
        aluno = self.criar()
        self.assertEqual(aluno.responsavel_financeiro_id, r.pk)
        r.refresh_from_db()
        self.assertEqual(r.asaas_customer_id, 'cus_preservado')
        self.assertEqual(Responsavel.objects.count(), 1)

    def test_reutiliza_sem_cpf_por_nome_whatsapp(self):
        self.payload['responsavel_cpf'] = ''
        r = Responsavel.objects.create(academia=self.a, nome='Maria', whatsapp='(21) 99999-9999')
        self.assertEqual(self.criar().responsavel_financeiro_id, r.pk)

    def test_responsavel_existente(self):
        r = Responsavel.objects.create(academia=self.a, nome='Maria', whatsapp='21')
        for key in ('responsavel_nome', 'responsavel_cpf', 'responsavel_whatsapp'):
            self.payload.pop(key)
        self.payload['responsavel'] = r.pk
        self.assertEqual(self.criar().responsavel_financeiro_id, r.pk)

    def test_ids_de_outra_academia_sao_rejeitados(self):
        r = Responsavel.objects.create(academia=self.b, nome='Segredo', whatsapp='21')
        t = Turma.objects.create(academia=self.b, modalidade=self.outro, nome='Segredo turma')
        for field, value in [('responsavel', r.pk), ('matricula-modalidade', self.outro.pk), ('matricula-turma', t.pk)]:
            with self.subTest(field=field):
                response = self.client.post('/alunos/novo/', {**self.payload, field: value})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(Atleta.objects.count(), 0)
        self.assertNotContains(self.client.get('/alunos/novo/'), 'Segredo')

    def test_detalhe_edicao_listagem_isolados(self):
        aluno = Atleta.objects.create(academia=self.b, nome='Aluno secreto')
        for suffix in ('', 'editar/'):
            url = f'/alunos/{aluno.pk}/{suffix}'
            self.assertEqual(self.client.get(url).status_code, 404)
            self.assertEqual(self.client.post(url, self.payload).status_code, 404)
        self.assertNotContains(self.client.get('/alunos/'), 'Aluno secreto')

    def test_validacoes_matricula(self):
        turma = Turma.objects.create(academia=self.a, modalidade=Modalidade.objects.create(academia=self.a, nome='Outro'), nome='Outra')
        for changes in ({'matricula-dia_vencimento': 32}, {'matricula-valor_mensalidade': '-1'}, {'matricula-data_fim': '2026-08-01'}, {'matricula-turma': turma.pk}, {'data_nascimento': '2999-01-01'}):
            with self.subTest(changes=changes):
                self.assertEqual(self.client.post('/alunos/novo/', {**self.payload, **changes}).status_code, 200)
                self.assertFalse(Atleta.objects.exists())
                self.assertFalse(Responsavel.objects.exists())

    def test_atomic_reverte_responsavel_e_aluno(self):
        with patch('portal.views.Matricula.save', side_effect=ValidationError('Falha de matrícula')):
            self.assertContains(self.client.post('/alunos/novo/', self.payload), 'Falha de matrícula')
        self.assertFalse(Atleta.objects.exists())
        self.assertFalse(Responsavel.objects.exists())
        self.assertFalse(Matricula.objects.exists())

    def test_edicao_preserva_matriculas_e_edita_selecionada(self):
        aluno = self.criar()
        m = aluno.matriculas.get()
        dados = {'nome': 'João atualizado', 'status': 'ativo', 'responsavel': aluno.responsavel_financeiro_id}
        self.assertEqual(self.client.post(f'/alunos/{aluno.pk}/editar/', dados).status_code, 302)
        self.assertEqual(Matricula.objects.count(), 1)
        dados.update({k: v for k, v in self.payload.items() if k.startswith('matricula-')})
        dados['matricula-valor_mensalidade'] = '180'
        url = reverse('portal:editar_matricula', args=[aluno.pk, m.pk])
        self.assertEqual(self.client.post(url, dados).status_code, 302)
        m.refresh_from_db()
        self.assertEqual(m.valor_mensalidade, 180)
        self.assertEqual(Matricula.objects.count(), 1)

    def test_csrf_cadastro(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.post('/alunos/novo/', self.payload).status_code, 403)

    def test_matricula_de_outro_aluno_nao_pode_ser_editada(self):
        aluno = self.criar()
        outro = Atleta.objects.create(academia=self.a, nome='Outro aluno')
        m = aluno.matriculas.get()
        url = reverse('portal:editar_matricula', args=[outro.pk, m.pk])
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.post(url, self.payload).status_code, 404)

    def test_cpf_de_outra_academia_nao_e_reutilizado(self):
        r = Responsavel.objects.create(academia=self.b, nome='Outro responsável', cpf='12345678900', whatsapp='21')
        self.assertNotEqual(self.criar().responsavel_financeiro_id, r.pk)

    def test_falha_na_edicao_reverte_aluno(self):
        aluno = self.criar()
        dados = {k: v for k, v in self.payload.items() if k.startswith('matricula-')}
        dados.update(nome='Nome alterado', status='ativo', responsavel=aluno.responsavel_financeiro_id)
        url = reverse('portal:editar_matricula', args=[aluno.pk, aluno.matriculas.get().pk])
        with patch('portal.views.Matricula.save', side_effect=ValidationError('Falha')):
            self.assertEqual(self.client.post(url, dados).status_code, 200)
        aluno.refresh_from_db()
        self.assertEqual(aluno.nome, 'João')

    def test_relacao_legada_inconsistente_nao_expoe_responsavel(self):
        r = Responsavel.objects.create(academia=self.b, nome='Responsável secreto', whatsapp='21')
        aluno = Atleta.objects.create(academia=self.a, nome='Aluno', responsavel_financeiro=r)
        self.assertNotContains(self.client.get(reverse('portal:detalhe', args=[aluno.pk])), 'Responsável secreto')

    def test_busca_e_paginacao(self):
        Atleta.objects.bulk_create([Atleta(academia=self.a, nome=f'Aluno {i:02}') for i in range(25)])
        response = self.client.get('/alunos/')
        self.assertEqual(len(response.context['page_obj']), 20)
        self.assertEqual(len(self.client.get('/alunos/?page=2').context['page_obj']), 5)
        self.assertContains(self.client.get('/alunos/?q=Aluno+24'), 'Aluno 24')
        self.assertNotContains(self.client.get('/alunos/?q=Aluno+24'), 'Aluno 00')

    def test_filtro_situacao_com_busca_e_isolamento(self):
        Atleta.objects.create(academia=self.a, nome='Ana ativa', status='ativo')
        Atleta.objects.create(academia=self.a, nome='Ana inativa', status='inativo')
        Atleta.objects.create(academia=self.b, nome='Ana secreta', status='ativo')
        response = self.client.get('/alunos/?q=Ana&status=ativo')
        self.assertContains(response, 'Ana ativa')
        self.assertNotContains(response, 'Ana inativa')
        self.assertNotContains(response, 'Ana secreta')
        self.assertEqual(response.context['page_obj'].paginator.count, 1)

    def test_dashboard_recentes_somente_da_academia(self):
        for i in range(6):
            Atleta.objects.create(academia=self.a, nome=f'Recente {i}')
        Atleta.objects.create(academia=self.b, nome='Recente secreto')
        response = self.client.get('/painel/')
        self.assertEqual(len(response.context['recentes']), 5)
        self.assertContains(response, 'Recente 5')
        self.assertNotContains(response, 'Recente 0')
        self.assertNotContains(response, 'Recente secreto')


class PaginaPublicaTests(TestCase):
    def test_pagina_acessivel_sem_login(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Keiko Fukuda')
        self.assertContains(response, 'https://www.instagram.com/escoladejudokeikofukuda/')
        self.assertEqual(self.client.get('/painel/').status_code, 302)

    def test_somente_fotos_publicadas_e_texto_escapado(self):
        from .models import PaginaPublica, FotoPublica
        PaginaPublica.objects.create(historia='<script>alert(1)</script>')
        FotoPublica.objects.create(titulo='Foto privada', url='https://example.com/private.jpg')
        FotoPublica.objects.create(titulo='Foto aprovada', url='https://example.com/public.jpg', publicada=True)
        response = self.client.get('/')
        self.assertContains(response, 'Foto aprovada')
        self.assertNotContains(response, 'Foto privada')
        self.assertNotContains(response, '<script>alert(1)</script>')
