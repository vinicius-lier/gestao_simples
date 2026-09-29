from datetime import date, datetime, time, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from academias.models import Academia, Unidade
from atletas.models import Atleta, Responsavel
from financeiro.lembretes import enviar_lembretes
from financeiro.models import CobrancaPix, LembreteCobranca, Mensalidade
from financeiro.services import (
    gerar_mensalidade_inicial, gerar_taxa_matricula, registrar_pagamento, resumo_financeiro,
)
from integracoes.whatsapp.evolution import contexto_cobranca
from integracoes.woovi.services import garantir_cobranca_pix, registrar_pagamento_pix
from matriculas.models import Matricula
from modalidades.models import Modalidade, Turma
from portal.models import AcessoAcademia, ConviteMatricula
from portal.services_matricula import ativar_convite, ativar_matricula
from tests.woovi_base import CenarioWoovi, cobranca_criada

HOJE = timezone.localdate


class Cenario(TestCase):
    def setUp(self):
        self.academia = Academia.objects.create(nome='Keiko', cnpj='K1')
        self.modalidade = Modalidade.objects.create(academia=self.academia, nome='Judô')
        self.turma = Turma.objects.create(
            academia=self.academia, modalidade=self.modalidade, nome='Kids',
            valor_mensalidade=Decimal('150.00'), valor_apos_vencimento=Decimal('170.00'),
            taxa_matricula=Decimal('80.00'), dia_vencimento=10,
        )
        self.responsavel = Responsavel.objects.create(academia=self.academia, nome='Maria', whatsapp='21999998888')
        self.aluno = Atleta.objects.create(academia=self.academia, nome='Ana', responsavel_financeiro=self.responsavel)

    def matricula(self, **kw):
        dados = dict(
            academia=self.academia, atleta=self.aluno, modalidade=self.modalidade, turma=self.turma,
            valor_mensalidade=Decimal('150.00'), valor_apos_vencimento=Decimal('170.00'),
            taxa_matricula=Decimal('80.00'), dia_vencimento=10, data_inicio=HOJE(),
        )
        dados.update(kw)
        return Matricula.objects.create(**dados)

    def mensalidade(self, vencimento, **kw):
        dados = dict(
            academia=self.academia, matricula=kw.pop('matricula', None) or self.matricula(),
            competencia=vencimento.replace(day=1), valor=Decimal('150.00'),
            valor_apos_vencimento=Decimal('170.00'), vencimento=vencimento,
        )
        dados.update(kw)
        return Mensalidade.objects.create(**dados)


class ValorAposVencimentoTests(Cenario):
    def test_mensalidade_criada_copia_o_valor_da_matricula(self):
        mensalidade = gerar_mensalidade_inicial(self.matricula())
        self.assertEqual(mensalidade.valor_apos_vencimento, Decimal('170.00'))

    def test_bolsa_integral_nao_tem_valor_apos_vencimento(self):
        mensalidade = gerar_mensalidade_inicial(self.matricula(valor_mensalidade=Decimal('0')))
        self.assertEqual((mensalidade.status, mensalidade.valor_apos_vencimento), ('isenta', None))

    def test_valor_devido_muda_no_dia_seguinte_ao_vencimento(self):
        vencimento = date(2026, 10, 10)
        m = self.mensalidade(vencimento)
        self.assertEqual(m.valor_devido(vencimento), Decimal('150.00'))
        self.assertEqual(m.valor_devido(vencimento + timedelta(days=1)), Decimal('170.00'))
        sem = self.mensalidade(vencimento, matricula=m.matricula, competencia=date(2026, 11, 1), valor_apos_vencimento=None)
        self.assertEqual(sem.valor_devido(vencimento + timedelta(days=30)), Decimal('150.00'))

    def test_baixa_manual_grava_o_valor_do_dia_do_pagamento(self):
        atrasada = self.mensalidade(HOJE() - timedelta(days=3))
        registrar_pagamento(atrasada, 'dinheiro')
        atrasada.refresh_from_db()
        self.assertEqual((atrasada.valor_pago, atrasada.valor_atual), (Decimal('170.00'), Decimal('170.00')))

        em_dia = self.mensalidade(HOJE() + timedelta(days=3), matricula=atrasada.matricula, competencia=date(2099, 1, 1))
        registrar_pagamento(em_dia, 'dinheiro')
        em_dia.refresh_from_db()
        self.assertEqual(em_dia.valor_pago, Decimal('150.00'))

    def test_resumo_usa_o_valor_pago_e_o_devido(self):
        hoje = HOJE()
        if hoje.day == 1:
            self.skipTest('No dia 1 não há vencimento passado dentro do mês.')
        competencia = hoje.replace(day=1)
        vencida = self.mensalidade(hoje - timedelta(days=1), competencia=competencia)
        Mensalidade.objects.create(
            academia=self.academia, matricula=self.matricula(), competencia=competencia,
            valor=Decimal('150.00'), vencimento=hoje, status='paga', valor_pago=Decimal('170.00'),
        )
        resumo = resumo_financeiro(self.academia, competencia)
        vencida.refresh_from_db()
        self.assertEqual(vencida.status, 'vencida')
        self.assertEqual(resumo['previsto'], Decimal('300.00'))
        self.assertEqual(resumo['recebido'], Decimal('170.00'))
        self.assertEqual(resumo['atrasado'], Decimal('170.00'))
        self.assertEqual(resumo['a_receber'], Decimal('0'))

    def test_mensagem_avisa_do_valor_apos_o_vencimento(self):
        m = self.mensalidade(HOJE() + timedelta(days=5))
        texto = contexto_cobranca(m, '5_dias')
        self.assertIn('R$ 150.00', texto)
        self.assertIn('Após o vencimento, o valor passa a R$ 170.00.', texto)

        atrasada = self.mensalidade(HOJE() - timedelta(days=2), matricula=m.matricula, competencia=date(2099, 1, 1))
        texto = contexto_cobranca(atrasada, 'atrasada')
        self.assertIn('R$ 170.00', texto)
        self.assertNotIn('Após o vencimento', texto)


class TaxaMatriculaTests(Cenario):
    def test_primeira_ativacao_gera_a_taxa_com_vencimento_no_dia(self):
        matricula = self.matricula(ativo=False)
        with patch('financeiro.lembretes._enviar_cobranca_whatsapp', return_value={'provider': 'evolution', 'message_id': 'm1'}) as envio:
            with self.captureOnCommitCallbacks(execute=True):
                ativar_matricula(matricula, primeiro_vencimento=HOJE() + timedelta(days=10))

        taxa = Mensalidade.objects.get(tipo=Mensalidade.TAXA_MATRICULA)
        self.assertEqual((taxa.valor, taxa.vencimento, taxa.status), (Decimal('80.00'), HOJE(), 'pendente'))
        self.assertIsNone(taxa.valor_apos_vencimento)
        self.assertEqual(Mensalidade.objects.filter(tipo=Mensalidade.MENSALIDADE).count(), 1)
        # Mandada na hora, e registrada como o lembrete do dia:
        envio.assert_called_once()
        self.assertEqual(envio.call_args.args[2], taxa)
        lembrete = LembreteCobranca.objects.get(mensalidade=taxa)
        self.assertEqual((lembrete.estagio, lembrete.status), (LembreteCobranca.VENCIMENTO, LembreteCobranca.ENVIADO))
        with patch('financeiro.lembretes._enviar_cobranca_whatsapp') as envio_da_regua:
            enviar_lembretes()
        self.assertNotIn(taxa, [c.args[2] for c in envio_da_regua.call_args_list])

    def test_reativacao_nao_cobra_taxa(self):
        matricula = self.matricula(ativo=False)
        gerar_mensalidade_inicial(matricula)  # já foi cobrada antes
        ativar_matricula(matricula)
        self.assertFalse(Mensalidade.objects.filter(tipo=Mensalidade.TAXA_MATRICULA).exists())

    def test_sem_taxa_ou_taxa_zero_nao_gera(self):
        for taxa in (None, Decimal('0')):
            with self.subTest(taxa=taxa):
                self.assertIsNone(gerar_taxa_matricula(self.matricula(taxa_matricula=taxa)))

    def test_uma_taxa_por_matricula(self):
        matricula = self.matricula()
        self.assertEqual(gerar_taxa_matricula(matricula), gerar_taxa_matricula(matricula))
        self.assertEqual(Mensalidade.objects.filter(tipo=Mensalidade.TAXA_MATRICULA).count(), 1)

    def test_taxa_nao_conflita_com_a_mensalidade_do_mesmo_mes(self):
        matricula = self.matricula(primeiro_vencimento=HOJE())
        gerar_mensalidade_inicial(matricula)
        gerar_taxa_matricula(matricula)
        self.assertEqual(Mensalidade.objects.filter(matricula=matricula).count(), 2)

    def test_convite_ativado_usa_a_taxa_do_convite(self):
        convite = ConviteMatricula.gerar(
            academia=self.academia, modalidade=self.modalidade, turma=self.turma, taxa_matricula=Decimal('0'),
        )
        self.assertEqual(convite.taxa_matricula_efetiva(), Decimal('0'))
        matricula = self.matricula(ativo=False, taxa_matricula=convite.taxa_matricula_efetiva())
        ConviteMatricula.objects.filter(pk=convite.pk).update(
            status=ConviteMatricula.PREENCHIDO, matricula=matricula, atleta=self.aluno,
        )
        ativar_convite(convite)
        self.assertFalse(Mensalidade.objects.filter(tipo=Mensalidade.TAXA_MATRICULA).exists())

    def test_mensagem_da_taxa(self):
        taxa = gerar_taxa_matricula(self.matricula())
        texto = contexto_cobranca(taxa, 'vencimento')
        self.assertIn('taxa de matrícula de Ana', texto)
        self.assertIn('R$ 80.00', texto)
        self.assertEqual((taxa.referencia, taxa.descricao), ('Taxa de matrícula', 'Taxa de matrícula'))

    def test_cadastro_no_painel_ja_ativo_gera_a_taxa(self):
        usuario = get_user_model().objects.create_user('dona', password='x')
        AcessoAcademia.objects.create(usuario=usuario, academia=self.academia, administrador=True)
        self.client.force_login(usuario)
        with patch('financeiro.lembretes._enviar_cobranca_whatsapp', return_value={}):
            with self.captureOnCommitCallbacks(execute=True):
                resp = self.client.post('/alunos/novo/', {
                    'nome': 'Novo', 'status': 'ativo', 'responsavel': self.responsavel.pk,
                    'matricula-modalidade': self.modalidade.pk, 'matricula-turma': self.turma.pk,
                    'matricula-data_inicio': HOJE().isoformat(), 'matricula-ativo': 'on',
                })
        self.assertEqual(resp.status_code, 302)
        matricula = Matricula.objects.get(atleta__nome='Novo')
        # Em branco no formulário: vêm da turma.
        self.assertEqual((matricula.valor_apos_vencimento, matricula.taxa_matricula), (Decimal('170.00'), Decimal('80.00')))
        self.assertTrue(Mensalidade.objects.filter(matricula=matricula, tipo=Mensalidade.TAXA_MATRICULA).exists())


@patch('integracoes.woovi.services.WooviClient')
class PixComValorAposVencimentoTests(CenarioWoovi, TestCase):
    def setUp(self):
        self.criar_cenario()
        self.usar_conta_propria()

    def em_aberto(self, vencimento):
        self.mensalidade.vencimento = vencimento
        self.mensalidade.competencia = vencimento.replace(day=1)
        self.mensalidade.valor_apos_vencimento = Decimal('140.00')
        self.mensalidade.save()
        return self.mensalidade

    def test_pix_em_dia_expira_no_fim_do_vencimento(self, mock_client):
        mock_client.return_value.criar_cobranca.side_effect = cobranca_criada
        vencimento = HOJE() + timedelta(days=3)
        garantir_cobranca_pix(self.em_aberto(vencimento))
        kwargs = mock_client.return_value.criar_cobranca.call_args.kwargs
        self.assertEqual(kwargs['valor_centavos'], 12000)
        fim = timezone.make_aware(datetime.combine(vencimento, time.max))
        self.assertAlmostEqual(kwargs['expira_em_segundos'], (fim - timezone.now()).total_seconds(), delta=5)

    def test_depois_do_vencimento_o_pix_antigo_e_trocado_pelo_valor_maior(self, mock_client):
        mock_client.return_value.criar_cobranca.side_effect = cobranca_criada
        mensalidade = self.em_aberto(HOJE() - timedelta(days=1))
        antiga = self.cobranca(mensalidade=mensalidade)  # R$ 120, ainda vigente

        nova = garantir_cobranca_pix(mensalidade)

        self.assertNotEqual(nova, antiga)
        self.assertEqual(nova.valor, Decimal('140.00'))
        self.assertEqual(mock_client.return_value.criar_cobranca.call_args.kwargs['valor_centavos'], 14000)
        antiga.refresh_from_db()
        self.assertEqual(antiga.status, CobrancaPix.CANCELADA)

    def test_pagamento_pelo_pix_grava_o_valor_pago(self, mock_client):
        mensalidade = self.em_aberto(HOJE() - timedelta(days=1))
        cobranca = self.cobranca(mensalidade=mensalidade)
        cobranca.valor = Decimal('140.00')
        cobranca.save()
        registrar_pagamento_pix(cobranca, taxa_centavos=80, valor_pago_centavos=14000)
        mensalidade.refresh_from_db()
        self.assertEqual((mensalidade.status, mensalidade.valor_pago), ('paga', Decimal('140.00')))

    def test_pix_da_taxa_de_matricula(self, mock_client):
        mock_client.return_value.criar_cobranca.side_effect = cobranca_criada
        self.matricula.taxa_matricula = Decimal('60.00')
        self.matricula.save()
        taxa = gerar_taxa_matricula(self.matricula)
        garantir_cobranca_pix(taxa)
        kwargs = mock_client.return_value.criar_cobranca.call_args.kwargs
        self.assertEqual((kwargs['valor_centavos'], kwargs['comentario']), (6000, 'Taxa de matrícula - Ana'))
