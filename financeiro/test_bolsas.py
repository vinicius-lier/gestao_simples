from datetime import date
from decimal import Decimal
from io import StringIO

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase

from academias.models import Academia, TabelaMensalidade, Unidade
from atletas.models import Atleta
from modalidades.models import Modalidade, Turma
from matriculas.models import Matricula
from financeiro.services import _criar_ou_obter_mensalidade, proximo_vencimento


class BolsasTests(TestCase):
    def setUp(self):
        self.academia = Academia.objects.create(nome='Keiko', cnpj='bolsas')
        self.modalidade = Modalidade.objects.create(academia=self.academia, nome='Judô')
        self.aluno = Atleta.objects.create(academia=self.academia, nome='Aluno')
        self.matricula = Matricula.objects.create(academia=self.academia, atleta=self.aluno,
            modalidade=self.modalidade, valor_mensalidade=100, valor_apos_vencimento=120,
            dia_vencimento=10, data_inicio=date(2026, 1, 1))
        TabelaMensalidade.objects.create(academia=self.academia, ano=2027, valor=130,
            valor_apos_vencimento=150, dia_vencimento=15)

    def parcela(self, ano=2027, mes=1):
        return _criar_ou_obter_mensalidade(self.matricula, ano, mes)[0]

    def test_tabela_comeca_em_janeiro(self):
        antiga = self.parcela(2026, 12)
        nova = self.parcela()
        self.assertEqual(antiga.valor, 100)
        self.assertEqual(nova.valor, 130)
        self.assertEqual(nova.vencimento, date(2027, 1, 15))
        self.assertEqual(self.parcela(2028).valor, 130)
        self.assertEqual(proximo_vencimento(self.matricula, date(2026, 12, 20)), date(2027, 1, 15))

    def test_irmaos_so_em_dia(self):
        self.aluno.desconto_irmaos = True
        self.aluno.save()
        parcela = self.parcela()
        self.assertEqual(parcela.valor_devido(date(2027, 1, 15)), Decimal('117'))
        self.assertEqual(parcela.valor_devido(date(2027, 1, 16)), Decimal('150'))

    def test_bolsa_integral_nao_cobra_mesmo_atrasada(self):
        self.aluno.bolsa_percentual = 100
        self.aluno.beneficio_inicio = date(2027, 1, 1)
        self.aluno.save()
        self.assertEqual(self.parcela(2026, 12).valor, 100)
        parcela = self.parcela()
        self.assertEqual(parcela.status, 'isenta')
        self.assertEqual(parcela.valor, 0)
        self.assertIsNone(parcela.valor_apos_vencimento)

    def test_bolsa_parcial_nao_acumula_e_expira(self):
        self.aluno.bolsa_percentual = Decimal('50')
        self.aluno.desconto_irmaos = True
        self.aluno.beneficio_fim = date(2027, 1, 31)
        self.aluno.save()
        parcela = self.parcela()
        self.assertEqual(parcela.valor, 65)
        self.assertEqual(parcela.valor_apos_vencimento, 75)
        self.assertEqual(self.parcela(mes=2).valor, 130)

    def test_nao_reescreve_cobranca_emitida(self):
        parcela = self.parcela()
        self.aluno.bolsa_percentual = 100
        self.aluno.save()
        self.assertEqual(self.parcela().pk, parcela.pk)
        self.assertEqual(self.parcela().valor, 130)

    def test_isencao_anterior_e_outra_academia(self):
        self.matricula.valor_mensalidade = 0
        self.assertEqual(self.parcela().status, 'isenta')
        outra = Academia.objects.create(nome='Outra', cnpj='outra')
        self.matricula.academia = outra
        self.matricula.valor_mensalidade = 80
        self.assertEqual(self.parcela(mes=2).valor, 80)

    def test_valida_percentual_e_periodo(self):
        for percentual in [-1, 101]:
            self.aluno.bolsa_percentual = percentual
            with self.assertRaises(ValidationError):
                self.aluno.full_clean()
        self.aluno.bolsa_percentual = 50
        self.aluno.beneficio_inicio = date(2027, 2, 1)
        self.aluno.beneficio_fim = date(2027, 1, 1)
        with self.assertRaises(ValidationError):
            self.aluno.full_clean()

    def test_carga_simula_e_reexecucao_nao_duplica(self):
        unidade = Unidade.objects.create(academia=self.academia, nome='Sede')
        bolsistas = [Atleta.objects.create(academia=self.academia, nome=f'Bolsista {i}').pk for i in range(5)]
        options = dict(academia=self.academia.pk, modalidade=self.modalidade.pk,
            unidade=unidade.pk, bolsistas=bolsistas, stdout=StringIO())
        call_command('configurar_keiko_2027', **options)
        self.assertEqual(Turma.objects.count(), 0)
        call_command('configurar_keiko_2027', aplicar=True, **options)
        call_command('configurar_keiko_2027', aplicar=True, **options)
        self.assertEqual(Turma.objects.count(), 10)
        self.assertEqual(Atleta.objects.filter(bolsa_percentual=100).count(), 5)
