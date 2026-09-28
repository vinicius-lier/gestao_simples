from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from academias.models import Academia
from atletas.models import Atleta, Responsavel
from financeiro.models import Mensalidade
from matriculas.models import Matricula
from modalidades.models import Modalidade
from portal.models import AcessoAcademia


class ResponsaveisTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.academia = Academia.objects.create(nome='Keiko', cnpj='K1')
        self.admin = User.objects.create_user('dona', password='x')
        AcessoAcademia.objects.create(usuario=self.admin, academia=self.academia, administrador=True)
        self.prof = User.objects.create_user('prof', password='x')
        AcessoAcademia.objects.create(usuario=self.prof, academia=self.academia)
        self.maria = Responsavel.objects.create(academia=self.academia, nome='Maria Souza', cpf='52998224725', whatsapp='21999998888')
        self.ana = Atleta.objects.create(academia=self.academia, nome='Ana Souza', responsavel_financeiro=self.maria)
        self.beto = Atleta.objects.create(academia=self.academia, nome='Beto Souza', responsavel_financeiro=self.maria)
        modalidade = Modalidade.objects.create(academia=self.academia, nome='Judô')
        matricula = Matricula.objects.create(
            academia=self.academia, atleta=self.ana, modalidade=modalidade,
            valor_mensalidade=Decimal('120.00'), dia_vencimento=15, data_inicio=date(2026, 1, 1),
        )
        Mensalidade.objects.create(
            academia=self.academia, matricula=matricula, competencia=date(2099, 1, 1),
            valor=Decimal('120.00'), vencimento=date(2099, 1, 15),
        )
        self.client.force_login(self.admin)

    def test_lista_mostra_dados_e_por_quem_responde(self):
        resp = self.client.get('/responsaveis/')
        for trecho in ('Maria Souza', '52998224725', '21999998888', 'Ana Souza', 'Beto Souza', 'R$ 120,00', 'Sem senha'):
            self.assertContains(resp, trecho)

    def test_busca_por_nome_cpf_ou_whatsapp(self):
        Responsavel.objects.create(academia=self.academia, nome='Outro', whatsapp='21911112222')
        for busca in ('maria', '529.982', '99999-8888'):
            resp = self.client.get('/responsaveis/', {'q': busca})
            self.assertContains(resp, 'Maria Souza', msg_prefix=busca)
            self.assertNotContains(resp, 'Outro', msg_prefix=busca)

    def test_perfil_mostra_os_alunos_e_as_cobrancas(self):
        resp = self.client.get(f'/responsaveis/{self.maria.pk}/')
        self.assertContains(resp, 'Responde por 2 alunos')
        self.assertContains(resp, 'Ana Souza')
        self.assertContains(resp, 'Beto Souza')
        self.assertContains(resp, 'Mensalidade 01/2099')
        # O perfil do aluno leva ao do responsável.
        self.assertContains(self.client.get(f'/alunos/{self.ana.pk}/'), f'/responsaveis/{self.maria.pk}/')

    def test_administrador_edita_os_dados(self):
        resp = self.client.post(f'/responsaveis/{self.maria.pk}/editar/', {
            'nome': 'Maria  de Souza', 'cpf': '529.982.247-25', 'whatsapp': '(21) 98888-7777', 'email': 'maria@x.com',
        })
        self.assertRedirects(resp, f'/responsaveis/{self.maria.pk}/')
        self.maria.refresh_from_db()
        self.assertEqual((self.maria.nome, self.maria.whatsapp, self.maria.email), ('Maria de Souza', '21988887777', 'maria@x.com'))

    def test_cpf_invalido_ou_de_outro_responsavel_e_recusado(self):
        Responsavel.objects.create(academia=self.academia, nome='João', cpf='11144477735', whatsapp='21911112222')
        for cpf in ('123', '111.444.777-35'):
            resp = self.client.post(f'/responsaveis/{self.maria.pk}/editar/', {
                'nome': 'Maria Souza', 'cpf': cpf, 'whatsapp': '21999998888', 'email': '',
            })
            self.assertIn('cpf', resp.context['form'].errors, cpf)
        self.maria.refresh_from_db()
        self.assertEqual(self.maria.cpf, '52998224725')

    def test_professor_ve_mas_nao_edita(self):
        self.client.force_login(self.prof)
        self.assertEqual(self.client.get(f'/responsaveis/{self.maria.pk}/').status_code, 200)
        self.assertNotContains(self.client.get(f'/responsaveis/{self.maria.pk}/'), 'Editar responsável')
        self.assertEqual(self.client.get(f'/responsaveis/{self.maria.pk}/editar/').status_code, 403)

    def test_responsavel_de_outra_academia_nao_aparece(self):
        outra = Academia.objects.create(nome='Outra', cnpj='O1')
        alheio = Responsavel.objects.create(academia=outra, nome='Secreto', whatsapp='21900000000')
        self.assertNotContains(self.client.get('/responsaveis/'), 'Secreto')
        self.assertEqual(self.client.get(f'/responsaveis/{alheio.pk}/').status_code, 404)

    def test_enviar_acesso_ao_portal(self):
        with patch('integracoes.whatsapp.enviar_acesso') as enviar:
            resp = self.client.post(f'/responsaveis/{self.maria.pk}/acesso/')
        self.assertRedirects(resp, f'/responsaveis/{self.maria.pk}/')
        self.assertEqual(enviar.call_args.args[1], self.maria)
