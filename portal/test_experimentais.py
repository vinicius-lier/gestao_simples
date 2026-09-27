from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from academias.models import Academia, Unidade
from modalidades.models import Modalidade, Turma
from portal.models import (
    AcessoAcademia,
    AulaExperimental,
    ConfiguracaoExperimental,
    InscricaoExperimental,
)
from portal.services_experimentais import alterar_status, inscrever


class BaseExperimental(TestCase):
    def setUp(self):
        self.a = Academia.objects.create(nome='A', cnpj='A')
        self.b = Academia.objects.create(nome='B', cnpj='B')
        User = get_user_model()
        self.admin = User.objects.create_user('admin', password='x')
        AcessoAcademia.objects.create(usuario=self.admin, academia=self.a, administrador=True)
        self.staff = User.objects.create_user('staff', password='x')
        AcessoAcademia.objects.create(usuario=self.staff, academia=self.a, administrador=False)
        self.outro = User.objects.create_user('outro', password='x')
        AcessoAcademia.objects.create(usuario=self.outro, academia=self.b, administrador=True)

        self.unit = Unidade.objects.create(academia=self.a, nome='Matriz')
        self.mod = Modalidade.objects.create(academia=self.a, nome='Judô')
        self.turma = Turma.objects.create(academia=self.a, modalidade=self.mod, unidade=self.unit, nome='Infantil')
        self.aula = AulaExperimental.objects.create(
            turma=self.turma,
            inicio=timezone.now() + timedelta(days=2),
            fim=timezone.now() + timedelta(days=9),
            vagas=1,
        )

    def dados(self, **kw):
        base = dict(nome='Fulano de Tal', idade=30, responsavel='', telefone='21999999999', email='')
        base.update(kw)
        return base


class RegrasDeInscricao(BaseExperimental):
    def test_aula_aberta_e_visivel(self):
        self.assertTrue(self.aula.aberta)
        self.assertTrue(self.aula.disponivel_no_site())

    def test_confirma_quando_ha_vaga(self):
        insc = inscrever(self.aula.pk, self.dados())
        self.assertEqual(insc.status, 'confirmado')
        self.assertEqual(self.aula.vagas_disponiveis, 0)

    def test_lotada_exige_opt_in_de_fila(self):
        inscrever(self.aula.pk, self.dados())
        with self.assertRaises(ValidationError):
            inscrever(self.aula.pk, self.dados(nome='Beltrano', telefone='21988888888'))
        espera = inscrever(self.aula.pk, self.dados(nome='Beltrano', telefone='21988888888'), aceitar_fila=True)
        self.assertEqual(espera.status, 'espera')

    def test_fila_desabilitada_recusa_mesmo_com_opt_in(self):
        self.aula.fila_habilitada = False
        self.aula.save(update_fields=['fila_habilitada'])
        inscrever(self.aula.pk, self.dados())
        with self.assertRaises(ValidationError):
            inscrever(self.aula.pk, self.dados(nome='Beltrano', telefone='21988888888'), aceitar_fila=True)

    def test_participante_duplicado(self):
        self.aula.vagas = 2
        self.aula.save(update_fields=['vagas'])
        inscrever(self.aula.pk, self.dados())
        with self.assertRaises(ValidationError):
            inscrever(self.aula.pk, self.dados())

    def test_config_desliga_o_site(self):
        ConfiguracaoExperimental.objects.create(academia=self.a, ativo=False)
        self.assertFalse(self.aula.disponivel_no_site())
        with self.assertRaises(ValidationError):
            inscrever(self.aula.pk, self.dados())

    def test_fora_da_janela_de_antecedencia(self):
        ConfiguracaoExperimental.objects.create(academia=self.a, janela_dias=1)
        self.assertFalse(self.aula.disponivel_no_site())
        with self.assertRaises(ValidationError):
            inscrever(self.aula.pk, self.dados())

    def test_periodo_encerrado_recusa(self):
        AulaExperimental.objects.filter(pk=self.aula.pk).update(
            inicio=timezone.now() - timedelta(days=3), fim=timezone.now() - timedelta(days=1)
        )
        self.aula.refresh_from_db()
        self.assertFalse(self.aula.aberta)
        with self.assertRaises(ValidationError):
            inscrever(self.aula.pk, self.dados())

    def test_oferta_em_andamento_ainda_aceita(self):
        AulaExperimental.objects.filter(pk=self.aula.pk).update(
            inicio=timezone.now() - timedelta(hours=1), fim=timezone.now() + timedelta(days=2)
        )
        self.aula.refresh_from_db()
        self.assertTrue(self.aula.aberta)
        self.assertEqual(inscrever(self.aula.pk, self.dados()).status, 'confirmado')

    def test_fim_antes_do_inicio_e_invalido(self):
        aula = AulaExperimental(
            turma=self.turma,
            inicio=timezone.now() + timedelta(days=5),
            fim=timezone.now() + timedelta(days=4),
        )
        with self.assertRaises(ValidationError):
            aula.full_clean()


class FilaEStatus(BaseExperimental):
    def _fila(self, n):
        inscricoes = [inscrever(self.aula.pk, self.dados())]
        for i in range(1, n):
            inscricoes.append(
                inscrever(self.aula.pk, self.dados(nome=f'Pessoa {i}', telefone=f'2198888{i:04d}'), aceitar_fila=True)
            )
        return inscricoes

    def test_confirmar_primeiro_da_fila(self):
        a, b = self._fila(2)
        alterar_status(a, 'cancelado')
        alterar_status(b, 'confirmado')
        b.refresh_from_db()
        self.assertEqual(b.status, 'confirmado')

    def test_nao_confirma_fora_de_ordem(self):
        a, b, c = self._fila(3)
        alterar_status(a, 'cancelado')
        with self.assertRaises(ValidationError):
            alterar_status(c, 'confirmado')
        alterar_status(b, 'confirmado')

    def test_presenca_somente_apos_inicio(self):
        (a,) = self._fila(1)
        with self.assertRaises(ValidationError):
            alterar_status(a, 'presente')
        AulaExperimental.objects.filter(pk=self.aula.pk).update(inicio=timezone.now() - timedelta(hours=1))
        alterar_status(a, 'presente')
        a.refresh_from_db()
        self.assertEqual(a.status, 'presente')

    def test_convertido_e_terminal(self):
        (a,) = self._fila(1)
        AulaExperimental.objects.filter(pk=self.aula.pk).update(inicio=timezone.now() - timedelta(hours=1))
        alterar_status(a, 'presente')
        alterar_status(a, 'convertido')
        with self.assertRaises(ValidationError):
            alterar_status(a, 'faltou')


class Paginas(BaseExperimental):
    def test_landing_mostra_o_horario(self):
        resp = self.client.get('/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Infantil')
        self.assertContains(resp, 'Agende sua aula experimental')
        self.assertContains(resp, 'Agendar aula experimental')  # botão do herói

    def test_landing_esconde_secao_sem_aula(self):
        self.aula.delete()
        resp = self.client.get('/')
        self.assertNotContains(resp, 'Agende sua aula experimental')

    def test_pagina_publica_de_inscricao(self):
        resp = self.client.get(f'/experimental/{self.aula.pk}/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Enviar inscrição')

    def test_post_publico_cria_inscricao(self):
        resp = self.client.post(f'/experimental/{self.aula.pk}/', self.dados(), follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(InscricaoExperimental.objects.count(), 1)
        self.assertEqual(InscricaoExperimental.objects.get().status, 'confirmado')

    def test_menor_sem_responsavel_e_recusado(self):
        resp = self.client.post(f'/experimental/{self.aula.pk}/', self.dados(idade=9))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(InscricaoExperimental.objects.count(), 0)
        self.assertContains(resp, 'responsável pelo menor')


class Painel(BaseExperimental):
    def test_agenda_exige_login(self):
        self.assertEqual(self.client.get('/agenda/').status_code, 302)

    def test_staff_ve_agenda_mas_nao_cria(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get('/agenda/').status_code, 200)
        self.assertEqual(self.client.get('/agenda/nova/').status_code, 403)

    def test_admin_cria_aula_com_padroes_da_config(self):
        ConfiguracaoExperimental.objects.create(academia=self.a, vagas_padrao=7)
        self.client.force_login(self.admin)
        resp = self.client.get('/agenda/nova/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'value="7"')
        inicio = (timezone.now() + timedelta(days=3)).strftime('%Y-%m-%dT%H:%M')
        fim = (timezone.now() + timedelta(days=10)).strftime('%Y-%m-%dT%H:%M')
        abre = timezone.now().strftime('%Y-%m-%dT%H:%M')
        resp = self.client.post('/agenda/nova/', {
            'turma': self.turma.pk, 'inicio': inicio, 'fim': fim, 'vagas': 7,
            'inscricoes_abrem': abre, 'antecedencia_horas': 2,
            'fila_habilitada': 'on', 'ativa': 'on',
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(AulaExperimental.objects.filter(turma=self.turma).count(), 2)

    def test_configuracoes_somente_admin_salva(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get('/agenda/configuracoes/').status_code, 200)
        self.assertEqual(self.client.post('/agenda/configuracoes/', {
            'vagas_padrao': 5, 'antecedencia_horas_padrao': 2, 'janela_dias': 30,
        }).status_code, 403)
        self.client.force_login(self.admin)
        resp = self.client.post('/agenda/configuracoes/', {
            'ativo': 'on', 'vagas_padrao': 5, 'antecedencia_horas_padrao': 3,
            'janela_dias': 45, 'fila_habilitada_padrao': 'on',
        })
        self.assertEqual(resp.status_code, 302)
        cfg = ConfiguracaoExperimental.objects.get(academia=self.a)
        self.assertEqual((cfg.vagas_padrao, cfg.antecedencia_horas_padrao, cfg.janela_dias), (5, 3, 45))

    def test_status_isolado_por_academia(self):
        insc = inscrever(self.aula.pk, self.dados())
        self.client.force_login(self.outro)
        resp = self.client.post(f'/agenda/inscricoes/{insc.pk}/status/', {'status': 'cancelado'})
        self.assertEqual(resp.status_code, 404)
        insc.refresh_from_db()
        self.assertEqual(insc.status, 'confirmado')
