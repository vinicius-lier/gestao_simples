from django.test import TestCase

from academias.models import Academia
from atletas.models import Responsavel, Atleta


class AtletasTestCase(TestCase):

    def setUp(self):
        self.academia = Academia.objects.create(
            nome="Academia Teste"
        )

        self.responsavel = Responsavel.objects.create(
            academia=self.academia,
            nome="Maria da Silva",
            whatsapp="5524999999999",
        )

    def test_criar_responsavel(self):
        self.assertEqual(
            self.responsavel.nome,
            "Maria da Silva"
        )

        self.assertEqual(
            self.responsavel.academia,
            self.academia
        )

    def test_criar_atleta_com_responsavel(self):
        atleta = Atleta.objects.create(
            academia=self.academia,
            responsavel_financeiro=self.responsavel,
            nome="João da Silva",
            faixa="Amarela",
        )

        self.assertEqual(
            atleta.responsavel_financeiro,
            self.responsavel
        )

        self.assertEqual(
            atleta.academia,
            self.academia
        )

        self.assertEqual(
            atleta.status,
            "ativo"
        )