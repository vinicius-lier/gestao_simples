"""Carga idempotente e restrita à academia informada. Simula por padrão."""
from datetime import date, time
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from academias.models import Academia, Unidade, TabelaMensalidade
from atletas.models import Atleta
from modalidades.models import Modalidade, Turma


TURMAS = [
    ('BABY A', 'Segundas e quartas', 17, 15),
    ('BABY B1', 'Terças e quintas', 16, 15),
    ('BABY B2', 'Terças e quintas', 17, 15),
    ('SUB 9 A', 'Segundas e quartas', 18, 0),
    ('SUB 9 B', 'Terças e quintas', 18, 0),
    ('SUB 13 A', 'Segundas e quartas', 19, 0),
    ('SUB 13 B', 'Terças e quintas', 19, 0),
    ('JR/ SENIOR', 'Terças e quintas', 20, 0),
    ('FUNDAMENTOS A', 'Segundas', 20, 0),
    ('FUNDAMENTOS B', 'Quartas', 20, 0),
]


class Command(BaseCommand):
    help = 'Configura turmas, tabela de 2027 e cinco bolsas integrais da Keiko. Sem --aplicar, só simula.'

    def add_arguments(self, parser):
        parser.add_argument('--academia', type=int, required=True)
        parser.add_argument('--modalidade', type=int, required=True)
        parser.add_argument('--unidade', type=int, required=True)
        parser.add_argument('--bolsistas', type=int, nargs=5, required=True,
                            help='IDs dos cinco alunos previamente conferidos na academia.')
        parser.add_argument('--aplicar', action='store_true')

    @transaction.atomic
    def handle(self, *args, **options):
        academia = Academia.objects.get(pk=options['academia'])
        modalidade = Modalidade.objects.get(pk=options['modalidade'], academia=academia)
        unidade = Unidade.objects.get(pk=options['unidade'], academia=academia)
        # Dados pessoais ficam no banco, sem lista de nomes no repositório.
        bolsistas = list(Atleta.objects.filter(academia=academia, pk__in=options['bolsistas']))
        if len(bolsistas) != 5:
            raise CommandError('Informe cinco alunos distintos desta academia; carga cancelada.')
        for nome, dias, hora, minuto in TURMAS:
            existentes = Turma.objects.filter(academia=academia, modalidade=modalidade, nome=nome)
            if existentes.count() > 1:
                raise CommandError(f'Turma duplicada: {nome}; carga cancelada.')
            turma = existentes.first()
            self.stdout.write(f'TURMA: {nome}, {dias}, {hora:02d}:{minuto:02d}')
            if options['aplicar']:
                defaults = dict(unidade=unidade, dias_semana=dias, horario=time(hora, minuto), ativo=True)
                if turma:
                    for campo, valor in defaults.items():
                        setattr(turma, campo, valor)
                    turma.full_clean()
                    turma.save()
                else:
                    # Turmas novas recebem a referência de 2027; não mexe nos
                    # valores das matrículas existentes antes de janeiro.
                    Turma.objects.create(academia=academia, modalidade=modalidade, nome=nome,
                        valor_mensalidade=130, valor_apos_vencimento=150, dia_vencimento=15, **defaults)
        for aluno in bolsistas:
            self.stdout.write(f'BOLSA 100%: #{aluno.pk} {aluno.nome}, a partir de 01/01/2027')
            if options['aplicar']:
                aluno.bolsa_percentual = Decimal('100')
                aluno.beneficio_inicio = date(2027, 1, 1)
                aluno.beneficio_fim = None
                aluno.full_clean()
                aluno.save(update_fields=['bolsa_percentual', 'beneficio_inicio', 'beneficio_fim'])
        self.stdout.write('TABELA 2027: R$130 até dia 15; R$150 depois. Irmãos: R$117 em dia; R$150 depois.')
        if options['aplicar']:
            TabelaMensalidade.objects.update_or_create(academia=academia, ano=2027, defaults=dict(
                valor=130, valor_apos_vencimento=150, dia_vencimento=15, desconto_irmaos_percentual=10))
        self.stdout.write('APLICADO. Nenhuma cobrança existente foi alterada.' if options['aplicar'] else 'SIMULAÇÃO. Nada foi gravado.')
