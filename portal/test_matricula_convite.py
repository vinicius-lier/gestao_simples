from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from academias.models import Academia, IntegracaoWhatsApp, Unidade
from atletas.models import Atleta, Responsavel
from financeiro.models import Mensalidade
from integracoes.whatsapp.base import WhatsAppProviderError
from matriculas.models import Matricula
from modalidades.models import Modalidade, Turma
from portal.models import AcessoAcademia, ConviteMatricula, FichaMatricula
from portal.models_matricula import PERGUNTAS_SAUDE


class BaseConvite(TestCase):
    def setUp(self):
        User = get_user_model()
        self.a = Academia.objects.create(nome='A', cnpj='A')
        self.b = Academia.objects.create(nome='B', cnpj='B')
        self.prof = User.objects.create_user('prof', password='x')
        AcessoAcademia.objects.create(usuario=self.prof, academia=self.a, administrador=False)
        self.admin = User.objects.create_user('dona', password='x')
        AcessoAcademia.objects.create(usuario=self.admin, academia=self.a, administrador=True)
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
        """Ficha completa, como a família envia (sem termos: a academia A
        não tem texto de termos cadastrado)."""
        base = dict(
            nome='Aluno Teste', data_nascimento='2015-05-04', responsavel_nome='Mãe Teste',
            responsavel_cpf='529.982.247-25',
            telefone='(21) 99999-8888', email='mae@teste.com', autorizados_buscar='Mãe e avó',
            vencimento_preferido='10',
        )
        base.update({campo: 'nao' for campo, *_ in PERGUNTAS_SAUDE})
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
        resp = self.client.post(f'/matricula/{c.token}/', self.dados_familia(), follow=True)
        self.assertEqual(resp.status_code, 200)

        atleta = Atleta.objects.get()
        self.assertEqual(atleta.academia, self.a)
        responsavel = Responsavel.objects.get(academia=self.a, nome='Mãe Teste')
        self.assertEqual((responsavel.whatsapp, responsavel.email), ('21999998888', 'mae@teste.com'))
        mat = Matricula.objects.get()
        self.assertFalse(mat.ativo)
        self.assertEqual((mat.modalidade, mat.turma, mat.valor_mensalidade), (self.mod, self.turma, Decimal('150.00')))
        self.assertTrue(FichaMatricula.objects.filter(atleta=atleta, convite=c).exists())

        c.refresh_from_db()
        self.assertEqual(c.status, ConviteMatricula.PREENCHIDO)
        self.assertEqual((c.atleta, c.matricula), (atleta, mat))
        self.assertIsNotNone(c.preenchido_em)

    def test_sem_nome_do_responsavel_e_recusado(self):
        c = self.convite()
        resp = self.client.post(f'/matricula/{c.token}/', self.dados_familia(responsavel_nome=''))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Atleta.objects.count(), 0)
        self.assertIn('responsavel_nome', resp.context['form'].errors)

    def test_cpf_do_responsavel_e_obrigatorio_e_vai_para_o_cadastro(self):
        c = self.convite()
        for cpf in ('', '123', '111.111.111-11'):
            resp = self.client.post(f'/matricula/{c.token}/', self.dados_familia(responsavel_cpf=cpf))
            self.assertIn('responsavel_cpf', resp.context['form'].errors, cpf)
        self.assertEqual(Atleta.objects.count(), 0)
        self.client.post(f'/matricula/{c.token}/', self.dados_familia())
        self.assertEqual(Responsavel.objects.get().cpf, '52998224725')
        self.assertEqual(FichaMatricula.objects.get().responsavel_cpf, '52998224725')

    def test_pergunta_de_saude_sem_resposta_e_recusada(self):
        c = self.convite()
        dados = self.dados_familia()
        del dados['saude_tontura']
        resp = self.client.post(f'/matricula/{c.token}/', dados)
        self.assertEqual(Atleta.objects.count(), 0)
        self.assertIn('saude_tontura', resp.context['form'].errors)

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

    def ativar(self, c, **campos):
        dados = {'acao': 'ativar', 'dia_vencimento': 10,
                 'primeiro_vencimento': (timezone.localdate() + timedelta(days=5)).isoformat()}
        dados.update(campos)
        return self.client.post(f'/matriculas/convites/{c.pk}/acao/', dados)

    def test_administrador_define_o_vencimento_e_ativa(self):
        c = self._preenchido()
        self.client.force_login(self.admin)
        escolhido = timezone.localdate() + timedelta(days=5)
        resp = self.ativar(c, dia_vencimento=15, primeiro_vencimento=escolhido.isoformat())
        self.assertEqual(resp.status_code, 302)
        c.refresh_from_db()
        self.assertEqual(c.status, ConviteMatricula.ATIVADO)
        self.assertTrue(c.matricula.ativo)
        self.assertEqual((c.matricula.dia_vencimento, c.matricula.primeiro_vencimento), (15, escolhido))
        self.assertEqual(Mensalidade.objects.get().vencimento, escolhido)

    def test_tela_do_convite_sugere_o_vencimento_ao_administrador(self):
        c = self._preenchido()
        self.client.force_login(self.admin)
        resp = self.client.get(f'/matriculas/convites/{c.pk}/')
        self.assertContains(resp, 'Definir vencimento e ativar')
        self.assertContains(resp, 'name="primeiro_vencimento"')

    def test_professor_nao_ativa(self):
        c = self._preenchido()
        self.client.force_login(self.prof)
        self.assertEqual(self.ativar(c).status_code, 403)
        self.assertNotContains(self.client.get(f'/matriculas/convites/{c.pk}/'), 'name="primeiro_vencimento"')
        c.refresh_from_db()
        self.assertEqual(c.status, ConviteMatricula.PREENCHIDO)

    def test_vencimento_no_passado_nao_ativa(self):
        c = self._preenchido()
        self.client.force_login(self.admin)
        self.ativar(c, primeiro_vencimento=(timezone.localdate() - timedelta(days=1)).isoformat())
        c.refresh_from_db()
        self.assertEqual(c.status, ConviteMatricula.PREENCHIDO)
        self.assertFalse(Mensalidade.objects.exists())

    def test_nao_ativa_convite_pendente(self):
        c = self.convite()
        self.client.force_login(self.admin)
        self.ativar(c)
        c.refresh_from_db()
        self.assertEqual(c.status, ConviteMatricula.PENDENTE)

    def test_cancelar_convite(self):
        c = self.convite()
        self.client.force_login(self.prof)
        self.client.post(f'/matriculas/convites/{c.pk}/acao/', {'acao': 'cancelar'})
        c.refresh_from_db()
        self.assertEqual(c.status, ConviteMatricula.CANCELADO)


class AvisoParaAEscola(BaseConvite):
    """Depois que a família envia a ficha, a escola é avisada pelo WhatsApp
    para conferir, definir o vencimento e ativar."""

    def configurar_whatsapp(self, numero_avisos='21977776666'):
        IntegracaoWhatsApp.objects.create(
            academia=self.a, provider=IntegracaoWhatsApp.PROVIDER_EVOLUTION, numero_avisos=numero_avisos,
        )

    def enviar_ficha(self, **patch_kwargs):
        c = self.convite()
        with patch('integracoes.whatsapp.evolution.EvolutionWhatsAppProvider.enviar_aviso', **patch_kwargs) as aviso:
            resp = self.client.post(f'/matricula/{c.token}/', self.dados_familia())
        return c, resp, aviso

    def test_avisa_o_whatsapp_da_escola_com_o_link_do_convite(self):
        self.configurar_whatsapp()
        c, resp, aviso = self.enviar_ficha()
        self.assertEqual(resp.status_code, 302)
        telefone, texto = aviso.call_args.args
        self.assertEqual(telefone, '21977776666')
        self.assertIn('Aluno Teste', texto)
        self.assertIn(f'/matriculas/convites/{c.pk}/', texto)

    def test_sem_numero_de_avisos_a_familia_conclui_normalmente(self):
        self.configurar_whatsapp(numero_avisos='')
        c, resp, aviso = self.enviar_ficha()
        self.assertEqual(resp.status_code, 302)
        aviso.assert_not_called()
        c.refresh_from_db()
        self.assertEqual(c.status, ConviteMatricula.PREENCHIDO)

    def test_whatsapp_fora_do_ar_nao_atrapalha_a_familia(self):
        self.configurar_whatsapp()
        c, resp, _aviso = self.enviar_ficha(side_effect=WhatsAppProviderError('fora'))
        self.assertEqual(resp.status_code, 302)
        c.refresh_from_db()
        self.assertEqual(c.status, ConviteMatricula.PREENCHIDO)


class LinkPermanenteDoSite(BaseConvite):
    """A ficha no site (/matricula/): um link que várias famílias usam."""

    def criar_link(self):
        self.client.force_login(self.admin)
        resp = self.client.post('/matriculas/convites/', {
            'permanente': 'on', 'unidade': self.unit.pk, 'modalidade': self.mod.pk, 'turma': '',
            'valor_mensalidade': '120', 'valor_apos_vencimento': '130', 'dia_vencimento': '15',
        })
        self.assertEqual(resp.status_code, 302)
        self.client.logout()
        return ConviteMatricula.objects.get(permanente=True)

    def test_sem_link_o_site_diz_que_esta_fechado(self):
        resp = self.client.get('/matricula/')
        self.assertContains(resp, 'Matrículas pelo site fechadas')
        self.assertNotContains(self.client.get('/'), 'Fazer a matrícula')

    def test_varias_familias_pelo_mesmo_link(self):
        link = self.criar_link()
        self.assertIsNone(link.turma)
        self.assertContains(self.client.get('/'), 'Fazer a matrícula')
        self.assertContains(self.client.get('/matricula/'), 'Ficha de matrícula')

        for nome, telefone, cpf in (('Ana Lima', '21911112222', '11144477735'), ('Beto Souza', '21933334444', '22255588846')):
            resp = self.client.post('/matricula/', self.dados_familia(nome=nome, telefone=telefone, responsavel_cpf=cpf, responsavel_nome=f'Resp {nome}'))
            self.assertRedirects(resp, '/matricula/recebido/', fetch_redirect_response=False)

        link.refresh_from_db()
        self.assertEqual(link.status, ConviteMatricula.PENDENTE)  # continua aberto
        respostas = ConviteMatricula.objects.filter(link_origem=link)
        self.assertEqual(respostas.count(), 2)
        self.assertEqual(set(respostas.values_list('status', flat=True)), {ConviteMatricula.PREENCHIDO})
        for matricula in Matricula.objects.all():
            self.assertFalse(matricula.ativo)  # a escola confere e ativa
            self.assertIsNone(matricula.turma)
            self.assertEqual((matricula.valor_mensalidade, matricula.valor_apos_vencimento), (Decimal('120.00'), Decimal('130.00')))
            self.assertEqual(matricula.dia_vencimento, 15)  # o dia do link vale sobre a escolha da família
        self.assertEqual(FichaMatricula.objects.count(), 2)

        self.client.force_login(self.admin)
        detalhe = self.client.get(f'/matriculas/convites/{link.pk}/')
        self.assertContains(detalhe, 'Link permanente do site')
        self.assertContains(detalhe, 'Ana Lima')

    def test_cancelar_fecha_a_matricula_pelo_site(self):
        link = self.criar_link()
        self.client.force_login(self.admin)
        self.client.post(f'/matriculas/convites/{link.pk}/acao/', {'acao': 'cancelar'})
        self.client.logout()
        self.assertContains(self.client.get('/matricula/'), 'Matrículas pelo site fechadas')

    def test_sem_turma_exige_valor(self):
        self.client.force_login(self.admin)
        resp = self.client.post('/matriculas/convites/', {
            'permanente': 'on', 'unidade': self.unit.pk, 'modalidade': self.mod.pk, 'turma': '',
        })
        self.assertIn('valor_mensalidade', resp.context['form'].errors)
        self.assertFalse(ConviteMatricula.objects.exists())
