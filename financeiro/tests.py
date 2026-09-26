from datetime import date
from decimal import Decimal

from django.db import IntegrityError
from django.test import TestCase

from academias.models import Academia
from atletas.models import Atleta
from financeiro.models import Mensalidade
from financeiro.services import (
    gerar_mensalidades,
    gerar_mensalidades_do_dia,
    registrar_pagamento,
    resumo_financeiro,
)
from matriculas.models import Matricula
from modalidades.models import Modalidade


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
        self.modalidade = Modalidade.objects.create(
            academia=self.academia,
            nome="Jiu-jitsu",
        )
        self.matricula = Matricula.objects.create(
            academia=self.academia,
            atleta=self.atleta,
            modalidade=self.modalidade,
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
        self.matricula.data_inicio = date(2026, 9, 5)
        self.matricula.data_fim = date(2026, 9, 25)
        self.matricula.save(update_fields=["data_inicio", "data_fim"])

        resultado = gerar_mensalidades(ano=2026, mes=9)

        self.assertEqual(resultado, {"criadas": 1, "existentes": 0})
        self.assertTrue(Mensalidade.objects.exists())

    def test_inicio_depois_do_vencimento_comeca_no_mes_seguinte(self):
        # Entrou dia 20 com vencimento dia 10: setembro não gera uma
        # cobrança que já nasceria vencida; a 1ª é a de outubro.
        self.matricula.data_inicio = date(2026, 9, 20)
        self.matricula.save(update_fields=["data_inicio"])

        self.assertEqual(gerar_mensalidades(ano=2026, mes=9), {"criadas": 0, "existentes": 0})
        self.assertEqual(gerar_mensalidades(ano=2026, mes=10), {"criadas": 1, "existentes": 0})
        self.assertEqual(Mensalidade.objects.get().vencimento, date(2026, 10, 10))

    def test_nao_gera_para_aluno_trancado_ou_inativo(self):
        for status in ("trancado", "inativo"):
            self.atleta.status = status
            self.atleta.save(update_fields=["status"])

            resultado = gerar_mensalidades(ano=2026, mes=9)

            self.assertEqual(resultado, {"criadas": 0, "existentes": 0})
        self.assertFalse(Mensalidade.objects.exists())

    def test_vencimento_ate_limita_as_mensalidades_geradas(self):
        resultado = gerar_mensalidades(ano=2026, mes=9, vencimento_ate=date(2026, 9, 9))

        self.assertEqual(resultado, {"criadas": 0, "existentes": 0})

        resultado = gerar_mensalidades(ano=2026, mes=9, vencimento_ate=date(2026, 9, 10))

        self.assertEqual(resultado, {"criadas": 1, "existentes": 0})


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


class GerarMensalidadesDoDiaTests(TestCase):
    def setUp(self):
        academia = Academia.objects.create(nome="Academia Teste", cnpj="GMD1")
        atleta = Atleta.objects.create(academia=academia, nome="Atleta")
        modalidade = Modalidade.objects.create(academia=academia, nome="Judô")
        self.matricula = Matricula.objects.create(
            academia=academia, atleta=atleta, modalidade=modalidade,
            valor_mensalidade=Decimal("120.00"), dia_vencimento=3, data_inicio=date(2026, 1, 1),
        )

    def test_gera_o_mes_corrente(self):
        resultado = gerar_mensalidades_do_dia(hoje=date(2026, 9, 1))

        self.assertEqual(resultado, {"criadas": 1, "existentes": 0})
        self.assertEqual(Mensalidade.objects.get().competencia, date(2026, 9, 1))

    def test_antecipa_o_mes_seguinte_a_tempo_do_lembrete_de_5_dias(self):
        # 27/09 + 7 dias = 04/10: a mensalidade que vence em 03/10 já existe
        # no dia 27, então o lembrete de 5 dias antes (28/09) consegue sair.
        resultado = gerar_mensalidades_do_dia(hoje=date(2026, 9, 27))

        self.assertEqual(resultado, {"criadas": 2, "existentes": 0})
        self.assertEqual(
            sorted(Mensalidade.objects.values_list("vencimento", flat=True)),
            [date(2026, 9, 3), date(2026, 10, 3)],
        )

    def test_nao_antecipa_mes_seguinte_fora_da_janela(self):
        self.matricula.dia_vencimento = 20
        self.matricula.save(update_fields=["dia_vencimento"])

        gerar_mensalidades_do_dia(hoje=date(2026, 9, 27))

        self.assertEqual(
            list(Mensalidade.objects.values_list("competencia", flat=True)),
            [date(2026, 9, 1)],
        )

    def test_rodar_de_novo_no_mesmo_dia_nao_duplica(self):
        gerar_mensalidades_do_dia(hoje=date(2026, 9, 27))

        resultado = gerar_mensalidades_do_dia(hoje=date(2026, 9, 27))

        self.assertEqual(resultado, {"criadas": 0, "existentes": 2})
        self.assertEqual(Mensalidade.objects.count(), 2)

    def test_virada_de_ano(self):
        gerar_mensalidades_do_dia(hoje=date(2026, 12, 29))

        self.assertTrue(Mensalidade.objects.filter(competencia=date(2027, 1, 1)).exists())


class MensalidadeTestCase(TestCase):

    def setUp(self):
        self.academia = Academia.objects.create(
            nome="Academia Teste"
        )

        self.atleta = Atleta.objects.create(
            academia=self.academia,
            nome="João da Silva",
        )

        self.modalidade = Modalidade.objects.create(
            academia=self.academia,
            nome="Judô Infantil",
        )

        self.matricula = Matricula.objects.create(
            academia=self.academia,
            atleta=self.atleta,
            modalidade=self.modalidade,
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


class RegistrarPagamentoTests(TestCase):
    def setUp(self):
        self.academia = Academia.objects.create(nome="Academia Teste", cnpj="RP1")
        self.atleta = Atleta.objects.create(academia=self.academia, nome="Atleta")
        self.modalidade = Modalidade.objects.create(academia=self.academia, nome="Judô")
        self.matricula = Matricula.objects.create(
            academia=self.academia, atleta=self.atleta, modalidade=self.modalidade,
            valor_mensalidade=Decimal("120.00"), dia_vencimento=10, data_inicio=date(2026, 1, 1),
        )
        self.mensalidade = Mensalidade.objects.create(
            academia=self.academia, matricula=self.matricula, competencia=date(2026, 9, 1),
            valor=Decimal("120.00"), vencimento=date(2026, 9, 10), status="pendente",
        )

    def test_marca_como_paga_com_forma_e_data(self):
        registrar_pagamento(self.mensalidade, forma_pagamento="pix")
        self.mensalidade.refresh_from_db()
        self.assertEqual(self.mensalidade.status, "paga")
        self.assertEqual(self.mensalidade.forma_pagamento, "pix")
        self.assertIsNotNone(self.mensalidade.pago_em)

    def test_nao_permite_pagar_cancelada(self):
        self.mensalidade.status = "cancelada"
        self.mensalidade.save(update_fields=["status"])
        with self.assertRaisesMessage(ValueError, "cancelada não pode ser paga"):
            registrar_pagamento(self.mensalidade)


class MarcarVencidasTests(TestCase):
    def setUp(self):
        self.academia = Academia.objects.create(nome="Academia Teste", cnpj="MV1")
        self.atleta = Atleta.objects.create(academia=self.academia, nome="Atleta")
        self.modalidade = Modalidade.objects.create(academia=self.academia, nome="Judô")
        self.matricula = Matricula.objects.create(
            academia=self.academia, atleta=self.atleta, modalidade=self.modalidade,
            valor_mensalidade=Decimal("120.00"), dia_vencimento=10, data_inicio=date(2020, 1, 1),
        )

    def test_so_marca_pendente_vencida_como_vencida(self):
        vencida = Mensalidade.objects.create(
            academia=self.academia, matricula=self.matricula, competencia=date(2020, 1, 1),
            valor=Decimal("120.00"), vencimento=date(2020, 1, 10), status="pendente",
        )
        futura = Mensalidade.objects.create(
            academia=self.academia, matricula=self.matricula, competencia=date(2999, 1, 1),
            valor=Decimal("120.00"), vencimento=date(2999, 1, 10), status="pendente",
        )
        isenta = Mensalidade.objects.create(
            academia=self.academia, matricula=self.matricula, competencia=date(2020, 2, 1),
            valor=Decimal("0.00"), vencimento=date(2020, 2, 10), status="isenta",
        )
        Mensalidade.objects.marcar_vencidas()
        vencida.refresh_from_db(); futura.refresh_from_db(); isenta.refresh_from_db()
        self.assertEqual(vencida.status, "vencida")
        self.assertEqual(futura.status, "pendente")
        self.assertEqual(isenta.status, "isenta")


class ResumoFinanceiroTests(TestCase):
    def setUp(self):
        self.academia = Academia.objects.create(nome="Academia Teste", cnpj="RF1")
        self.outra = Academia.objects.create(nome="Outra", cnpj="RF2")
        self.modalidade = Modalidade.objects.create(academia=self.academia, nome="Judô")
        self.competencia = date(2026, 9, 1)

    def criar(self, status, valor, vencimento):
        # Uma mensalidade por matrícula/competência: cada chamada usa um
        # aluno (matrícula) novo, como em uma academia com vários alunos.
        atleta = Atleta.objects.create(academia=self.academia, nome=f"Atleta {status}")
        matricula = Matricula.objects.create(
            academia=self.academia, atleta=atleta, modalidade=self.modalidade,
            valor_mensalidade=Decimal(valor), dia_vencimento=10, data_inicio=date(2020, 1, 1),
        )
        return Mensalidade.objects.create(
            academia=self.academia, matricula=matricula, competencia=self.competencia,
            valor=Decimal(valor), vencimento=vencimento, status=status,
        )

    def test_previsto_recebido_a_receber_e_atrasado(self):
        self.criar("paga", "100.00", date(2026, 9, 10))
        self.criar("pendente", "100.00", date(2999, 1, 1))
        self.criar("vencida", "100.00", date(2020, 1, 1))
        self.criar("cancelada", "999.00", date(2020, 1, 1))
        outra_matricula = Matricula.objects.create(
            academia=self.outra, atleta=Atleta.objects.create(academia=self.outra, nome="De outra"),
            modalidade=Modalidade.objects.create(academia=self.outra, nome="Karatê"),
            valor_mensalidade=Decimal("500.00"), dia_vencimento=10, data_inicio=date(2020, 1, 1),
        )
        Mensalidade.objects.create(
            academia=self.outra, matricula=outra_matricula, competencia=self.competencia,
            valor=Decimal("500.00"), vencimento=date(2026, 9, 10), status="pendente",
        )

        resumo = resumo_financeiro(self.academia, self.competencia)

        self.assertEqual(resumo["previsto"], Decimal("300.00"))
        self.assertEqual(resumo["recebido"], Decimal("100.00"))
        self.assertEqual(resumo["atrasado"], Decimal("100.00"))
        self.assertEqual(resumo["a_receber"], Decimal("100.00"))
        self.assertAlmostEqual(float(resumo["inadimplencia"]), 100 / 3)