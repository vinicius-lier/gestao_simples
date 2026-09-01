from datetime import date
from decimal import Decimal

from django.db import IntegrityError
from django.test import TestCase

from academias.models import Academia
from atletas.models import Atleta
from matriculas.models import Matricula
from servicos.models import Servico
from financeiro.models import Mensalidade


class MensalidadeTestCase(TestCase):

    def setUp(self):
        self.academia = Academia.objects.create(
            nome="Academia Teste"
        )

        self.atleta = Atleta.objects.create(
            academia=self.academia,
            nome="João da Silva",
        )

        self.servico = Servico.objects.create(
            academia=self.academia,
            nome="Judô Infantil",
            valor_padrao=Decimal("150.00"),
            dia_vencimento=10,
        )

        self.matricula = Matricula.objects.create(
            academia=self.academia,
            atleta=self.atleta,
            servico=self.servico,
            valor_mensalidade=Decimal("150.00"),
            dia_vencimento=10,
            data_inicio=date(2026, 9, 1),
        )

    def test_nao_permite_mensalidade_duplicada(self):
        Mensalidade.objects.create(
            academia=self.academia,
            matricula=self.matricula,
            competencia=date(2026, 9, 1),
            valor=Decimal("150.00"),
            vencimento=date(2026, 9, 10),
        )

        with self.assertRaises(IntegrityError):
            Mensalidade.objects.create(
                academia=self.academia,
                matricula=self.matricula,
                competencia=date(2026, 9, 1),
                valor=Decimal("150.00"),
                vencimento=date(2026, 9, 10),
            )