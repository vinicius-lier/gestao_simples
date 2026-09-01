from datetime import date
from decimal import Decimal

from django.test import TestCase

from academias.models import Academia
from atletas.models import Atleta
from financeiro.models import Mensalidade
from financeiro.services import gerar_mensalidades
from matriculas.models import Matricula
from servicos.models import Servico


class GerarMensalidadesTests(TestCase):
    def setUp(self):
        self.academia = Academia.objects.create(
            nome="Academia Teste",
            cnpj="12.345.678/0001-90",
        )
        self.atleta = Atleta.objects.create(
            academia=self.academia,
            nome="Atleta Teste",
        )
        self.servico = Servico.objects.create(
            academia=self.academia,
            nome="Jiu-jitsu",
            valor_padrao=Decimal("150.00"),
            dia_vencimento=10,
        )
        self.matricula = Matricula.objects.create(
            academia=self.academia,
            atleta=self.atleta,
            servico=self.servico,
            valor_mensalidade=Decimal("135.50"),
            dia_vencimento=10,
            data_inicio=date(2026, 1, 15),
        )

    def test_cria_mensalidade_com_dados_da_matricula(self):
        resultado = gerar_mensalidades(ano=2026, mes=9)

        self.assertEqual(resultado, {"criadas": 1, "existentes": 0})
        mensalidade = Mensalidade.objects.get()
        self.assertEqual(mensalidade.academia, self.academia)
        self.assertEqual(mensalidade.matricula, self.matricula)
        self.assertEqual(mensalidade.competencia, date(2026, 9, 1))
        self.assertEqual(mensalidade.valor, Decimal("135.50"))
        self.assertEqual(mensalidade.vencimento, date(2026, 9, 10))
        self.assertEqual(mensalidade.status, "pendente")

    def test_nao_duplica_mensalidade_da_mesma_competencia(self):
        gerar_mensalidades(ano=2026, mes=9)

        resultado = gerar_mensalidades(ano=2026, mes=9)

        self.assertEqual(resultado, {"criadas": 0, "existentes": 1})
        self.assertEqual(Mensalidade.objects.count(), 1)

    def test_ajusta_vencimento_para_ultimo_dia_do_mes(self):
        self.matricula.dia_vencimento = 31
        self.matricula.save(update_fields=["dia_vencimento"])

        gerar_mensalidades(ano=2026, mes=2)

        self.assertEqual(
            Mensalidade.objects.get().vencimento,
            date(2026, 2, 28),
        )

    def test_nao_gera_para_matricula_inativa(self):
        self.matricula.ativo = False
        self.matricula.save(update_fields=["ativo"])

        resultado = gerar_mensalidades(ano=2026, mes=9)

        self.assertEqual(resultado, {"criadas": 0, "existentes": 0})
        self.assertFalse(Mensalidade.objects.exists())

    def test_nao_gera_antes_do_inicio_da_matricula(self):
        self.matricula.data_inicio = date(2026, 10, 1)
        self.matricula.save(update_fields=["data_inicio"])

        resultado = gerar_mensalidades(ano=2026, mes=9)

        self.assertEqual(resultado, {"criadas": 0, "existentes": 0})

    def test_nao_gera_depois_do_fim_da_matricula(self):
        self.matricula.data_fim = date(2026, 8, 31)
        self.matricula.save(update_fields=["data_fim"])

        resultado = gerar_mensalidades(ano=2026, mes=9)

        self.assertEqual(resultado, {"criadas": 0, "existentes": 0})

    def test_gera_quando_matricula_esteve_ativa_parte_do_mes(self):
        self.matricula.data_inicio = date(2026, 9, 20)
        self.matricula.data_fim = date(2026, 9, 25)
        self.matricula.save(update_fields=["data_inicio", "data_fim"])

        resultado = gerar_mensalidades(ano=2026, mes=9)

        self.assertEqual(resultado, {"criadas": 1, "existentes": 0})
        self.assertTrue(Mensalidade.objects.exists())


    def test_gerar_mensalidades_sem_duplicar(self):
        resultado_primeira_execucao = gerar_mensalidades(
            ano=2026,
            mes=9,
        )

        self.assertEqual(
            resultado_primeira_execucao["criadas"],
            1,
        )

        self.assertEqual(
            Mensalidade.objects.count(),
            1,
        )

        resultado_segunda_execucao = gerar_mensalidades(
            ano=2026,
            mes=9,
        )

        self.assertEqual(
            resultado_segunda_execucao["criadas"],
            0,
        )

        self.assertEqual(
            resultado_segunda_execucao["existentes"],
            1,
        )

        self.assertEqual(
            Mensalidade.objects.count(),
            1,
        )