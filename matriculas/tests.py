from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from academias.models import Academia
from atletas.models import Atleta
from matriculas.models import Matricula
from modalidades.models import Modalidade, Turma


class MatriculaTestCase(TestCase):

    def setUp(self):
        self.academia_a = Academia.objects.create(
            nome="Academia A",
            cnpj="11111111000111",
        )

        self.academia_b = Academia.objects.create(
            nome="Academia B",
            cnpj="22222222000122",
        )

        self.atleta_a = Atleta.objects.create(
            academia=self.academia_a,
            nome="João",
        )

        self.modalidade_a = Modalidade.objects.create(
            academia=self.academia_a,
            nome="Judô Infantil",
            valor_padrao=Decimal("150.00"),
            dia_vencimento=10,
        )

        self.modalidade_b = Modalidade.objects.create(
            academia=self.academia_b,
            nome="Judô Adulto",
            valor_padrao=Decimal("180.00"),
            dia_vencimento=10,
        )

        self.turma_a = Turma.objects.create(
            academia=self.academia_a,
            modalidade=self.modalidade_a,
            nome="Turma A",
        )

    def test_nao_permite_atleta_de_outra_academia(self):
        atleta_b = Atleta.objects.create(
            academia=self.academia_b,
            nome="Pedro",
        )

        with self.assertRaises(ValidationError):
            Matricula.objects.create(
                academia=self.academia_a,
                atleta=atleta_b,
                modalidade=self.modalidade_a,
                turma=self.turma_a,
                valor_mensalidade=Decimal("150.00"),
                dia_vencimento=10,
                data_inicio=date(2026, 9, 1),
            )

    def test_nao_permite_modalidade_de_outra_academia(self):
        with self.assertRaises(ValidationError):
            Matricula.objects.create(
                academia=self.academia_a,
                atleta=self.atleta_a,
                modalidade=self.modalidade_b,
                valor_mensalidade=Decimal("180.00"),
                dia_vencimento=10,
                data_inicio=date(2026, 9, 1),
            )

    def test_nao_permite_vencimento_maior_que_31(self):
        with self.assertRaises(ValidationError):
            Matricula.objects.create(
                academia=self.academia_a,
                atleta=self.atleta_a,
                modalidade=self.modalidade_a,
                turma=self.turma_a,
                valor_mensalidade=Decimal("150.00"),
                dia_vencimento=40,
                data_inicio=date(2026, 9, 1),
            )

    def test_nao_permite_data_fim_anterior_ao_inicio(self):
        with self.assertRaises(ValidationError):
            Matricula.objects.create(
                academia=self.academia_a,
                atleta=self.atleta_a,
                modalidade=self.modalidade_a,
                turma=self.turma_a, 
                valor_mensalidade=Decimal("150.00"),
                dia_vencimento=10,
                data_inicio=date(2026, 9, 10),
                data_fim=date(2026, 9, 1),
            )

    def test_permite_matricula_valida(self):
        matricula = Matricula.objects.create(
            academia=self.academia_a,
            atleta=self.atleta_a,
            modalidade=self.modalidade_a,
            turma=self.turma_a,
            valor_mensalidade=Decimal("150.00"),
            dia_vencimento=10,
            data_inicio=date(2026, 9, 1),
        )

        self.assertEqual(
            matricula.academia,
            self.academia_a
        )