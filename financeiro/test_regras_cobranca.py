"""Regras de início e fim da cobrança (auditoria de 09/2026): 1º
vencimento definido por quem cria ou ativa a matrícula, sem cobrança
retroativa; aluno que sai não gera mensalidade nova nem recebe lembrete;
vencida só depois das 23h59 de Brasília; bolsa integral; baixa manual sobre
pagamento já registrado; isenção no painel."""
from datetime import date, datetime, timedelta
from datetime import timezone as dt_timezone
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from academias.models import Academia
from atletas.models import Atleta, Responsavel
from financeiro.lembretes import enviar_lembretes
from financeiro.models import LembreteCobranca, Mensalidade
from financeiro.services import (
    gerar_mensalidades,
    gerar_mensalidades_do_dia,
    iniciar_cobranca,
    proximo_vencimento,
    registrar_pagamento,
    resumo_financeiro,
)
from integracoes.woovi.services import registrar_pagamento_pix
from matriculas.models import Matricula
from modalidades.models import Modalidade
from portal.models import AcessoAcademia
from tests.woovi_base import CenarioWoovi

HOJE = date(2026, 9, 27)
ENVIO_OK = {"provider": "fake", "message_id": "1"}


def proximo_mes(dia):
    return (dia.replace(day=1) + timedelta(days=32)).replace(day=1)


class Cenario(TestCase):
    def setUp(self):
        self.academia = Academia.objects.create(nome="Academia", cnpj="RC1")
        self.responsavel = Responsavel.objects.create(
            academia=self.academia, nome="Resp", whatsapp="21999998888",
        )
        self.atleta = Atleta.objects.create(
            academia=self.academia, nome="Aluno", responsavel_financeiro=self.responsavel,
        )
        self.modalidade = Modalidade.objects.create(academia=self.academia, nome="Judô")

    def matricula(self, **dados):
        campos = dict(
            academia=self.academia, atleta=self.atleta, modalidade=self.modalidade,
            valor_mensalidade=Decimal("120.00"), dia_vencimento=10, data_inicio=HOJE,
        )
        campos.update(dados)
        return Matricula.objects.create(**campos)

    def mensalidade(self, matricula, competencia, status="pendente", dia=10):
        return Mensalidade.objects.create(
            academia=self.academia, matricula=matricula, competencia=competencia,
            valor=matricula.valor_mensalidade, vencimento=competencia.replace(day=dia), status=status,
        )


class ProximoVencimentoTests(Cenario):
    """A sugestão de 1º vencimento quando ninguém escolhe outra data."""

    def test_quem_ja_treinava_comeca_no_proximo_vencimento(self):
        # Aluno desde 2023 cadastrado em 27/09: nada de 2023 nem de setembro
        # (dia 10 já passou).
        matricula = self.matricula(data_inicio=date(2023, 3, 1))
        self.assertEqual(proximo_vencimento(matricula, HOJE), date(2026, 10, 10))

    def test_vencimento_no_proprio_dia_vale(self):
        matricula = self.matricula(data_inicio=date(2026, 9, 1), dia_vencimento=27)
        self.assertEqual(proximo_vencimento(matricula, HOJE), HOJE)

    def test_dia_31_em_mes_curto(self):
        matricula = self.matricula(dia_vencimento=31)
        self.assertEqual(proximo_vencimento(matricula, date(2026, 2, 10)), date(2026, 2, 28))

    def test_sem_data_usa_hoje_ou_o_inicio_futuro(self):
        futura = self.matricula(data_inicio=timezone.localdate() + timedelta(days=40))
        self.assertGreaterEqual(proximo_vencimento(futura), futura.data_inicio)
        antiga = self.matricula(data_inicio=date(2023, 3, 1))
        self.assertGreaterEqual(proximo_vencimento(antiga), timezone.localdate())


class IniciarCobrancaTests(Cenario):
    def test_primeiro_vencimento_escolhido_vale_para_a_1a_mensalidade(self):
        # Matrícula com vencimento dia 10; quem cadastrou escolheu que a 1ª
        # vence em 20/10. As seguintes voltam para o dia 10.
        matricula = self.matricula(data_inicio=date(2023, 3, 1))

        primeira = iniciar_cobranca(matricula, date(2026, 10, 20))
        gerar_mensalidades(2026, 10)
        gerar_mensalidades(2026, 11)

        matricula.refresh_from_db()
        self.assertEqual(matricula.primeiro_vencimento, date(2026, 10, 20))
        self.assertEqual(
            list(Mensalidade.objects.order_by("vencimento").values_list("vencimento", flat=True)),
            [date(2026, 10, 20), date(2026, 11, 10)],
        )
        self.assertEqual(primeira.competencia, date(2026, 10, 1))

    def test_nada_vence_antes_do_primeiro_vencimento(self):
        matricula = self.matricula(data_inicio=date(2023, 3, 1), primeiro_vencimento=date(2026, 10, 10))

        gerar_mensalidades_do_dia(hoje=HOJE)
        gerar_mensalidades(2026, 9)
        gerar_mensalidades(2025, 5)

        self.assertFalse(Mensalidade.objects.filter(matricula=matricula).exists())

    def test_rotina_cria_a_1a_mensalidade_na_data_escolhida_se_faltar(self):
        self.matricula(data_inicio=date(2023, 3, 1), primeiro_vencimento=date(2026, 10, 3))

        gerar_mensalidades_do_dia(hoje=HOJE)  # antecipa até 04/10

        self.assertEqual(Mensalidade.objects.get().vencimento, date(2026, 10, 3))

    def test_sem_escolha_usa_o_proximo_vencimento(self):
        matricula = self.matricula(data_inicio=date(2023, 3, 1))

        primeira = iniciar_cobranca(matricula)

        self.assertEqual(primeira.vencimento, proximo_vencimento(matricula))
        self.assertGreaterEqual(primeira.vencimento, timezone.localdate())

    def test_aluno_trancado_nao_e_cobrado(self):
        self.atleta.status = "trancado"
        self.atleta.save()
        matricula = self.matricula()

        self.assertIsNone(iniciar_cobranca(matricula, date(2026, 10, 10)))
        self.assertFalse(Mensalidade.objects.exists())

    def test_valor_zero_vira_isenta_e_fica_fora_da_cobranca(self):
        matricula = self.matricula(valor_mensalidade=Decimal("0"))

        primeira = iniciar_cobranca(matricula, date(2026, 10, 10))
        gerar_mensalidades(2026, 11)

        self.assertEqual(primeira.status, "isenta")
        self.assertEqual(Mensalidade.objects.get(competencia=date(2026, 11, 1)).status, "isenta")
        self.assertFalse(Mensalidade.objects.em_aberto().exists())

    def test_academia_inativa_nao_gera_mensalidade(self):
        self.matricula(data_inicio=date(2026, 1, 1))
        self.academia.ativo = False
        self.academia.save()

        self.assertEqual(gerar_mensalidades(2026, 10), {"criadas": 0, "existentes": 0})


class QuemSaiTests(Cenario):
    """Trancado, inativo ou matrícula desativada: não gera mensalidade nova,
    mantém as que já estavam em aberto e sai da régua de lembretes."""

    def setUp(self):
        super().setUp()
        self.inscricao = self.matricula(data_inicio=date(2026, 1, 1))
        self.setembro = self.mensalidade(self.inscricao, date(2026, 9, 1))
        self.outubro = self.mensalidade(self.inscricao, date(2026, 10, 1))

    FORMAS_DE_SAIR = ("trancado", "inativo", "matricula")

    def sair(self, como):
        if como == "matricula":
            self.inscricao.ativo = False
            self.inscricao.save()
        else:
            self.atleta.status = como
            self.atleta.save()

    def voltar(self):
        self.atleta.status = "ativo"
        self.atleta.save()
        self.inscricao.ativo = True
        self.inscricao.save()

    def test_nao_gera_mensalidade_nova_e_mantem_as_abertas(self):
        for como in self.FORMAS_DE_SAIR:
            with self.subTest(como=como):
                self.sair(como)

                gerar_mensalidades(2026, 11)
                gerar_mensalidades(2026, 12)

                self.assertEqual(
                    sorted(Mensalidade.objects.values_list("status", flat=True)), ["pendente", "pendente"],
                )
                self.voltar()

    def lembretes(self, hoje=date(2026, 10, 5)):
        # 05/10: outubro está a 5 dias do vencimento e setembro, atrasada.
        LembreteCobranca.objects.all().delete()
        with patch("financeiro.lembretes._enviar_cobranca_whatsapp", return_value=ENVIO_OK) as envio:
            enviar_lembretes(hoje=hoje)
        return envio.call_count

    def test_so_aluno_ativo_recebe_lembrete(self):
        self.assertEqual(self.lembretes(), 2)
        for como in self.FORMAS_DE_SAIR:
            with self.subTest(como=como):
                self.sair(como)
                self.assertEqual(self.lembretes(), 0)
                self.voltar()

    def test_academia_inativa_nao_cobra_as_familias(self):
        self.academia.ativo = False
        self.academia.save()
        self.assertEqual(self.lembretes(), 0)

    def test_competencia_depois_do_fim_da_matricula_nao_recebe(self):
        Mensalidade.objects.filter(pk=self.setembro.pk).update(status="paga")
        self.inscricao.data_fim = date(2026, 9, 30)
        self.inscricao.save()
        self.assertEqual(self.lembretes(), 0)


class BaixaManualTests(Cenario):
    def setUp(self):
        super().setUp()
        self.mensal = self.mensalidade(self.matricula(), date(2026, 9, 1))

    def test_nao_sobrescreve_pagamento_ja_registrado(self):
        registrar_pagamento(self.mensal, forma_pagamento="pix")
        tela_desatualizada = Mensalidade.objects.get(pk=self.mensal.pk)
        tela_desatualizada.status = "pendente"

        with self.assertRaisesMessage(ValueError, "já está paga"):
            registrar_pagamento(tela_desatualizada, forma_pagamento="dinheiro")

        self.mensal.refresh_from_db()
        self.assertEqual(self.mensal.forma_pagamento, "pix")

    def test_isenta_nao_pode_ser_paga(self):
        Mensalidade.objects.filter(pk=self.mensal.pk).update(status="isenta")

        with self.assertRaisesMessage(ValueError, "isenta não pode ser paga"):
            registrar_pagamento(self.mensal)


class PixPagoDeIsentaTests(CenarioWoovi, TestCase):
    def setUp(self):
        self.criar_cenario()

    def test_registra_o_pix_e_alerta_sem_falhar_o_webhook(self):
        cobranca = self.cobranca()
        Mensalidade.objects.filter(pk=self.mensalidade.pk).update(status="isenta")

        with self.assertLogs("gestao.alertas", level="ERROR") as alerta:
            self.assertTrue(registrar_pagamento_pix(cobranca, taxa_centavos=85))

        self.assertIn("isenta", alerta.output[0])
        self.mensalidade.refresh_from_db()
        self.assertEqual(self.mensalidade.status, "isenta")


class ResumoComIsentaTests(Cenario):
    def test_isenta_nao_entra_no_previsto_nem_no_a_receber(self):
        self.mensalidade(self.matricula(), date(2026, 9, 1), status="isenta")

        resumo = resumo_financeiro(self.academia, date(2026, 9, 1))

        self.assertEqual((resumo["previsto"], resumo["a_receber"]), (0, 0))


class FusoHorarioTests(Cenario):
    """Vence às 23h59 do dia do vencimento, no horário de Brasília."""

    # 02h59 UTC de 15/01 = 23h59 de 14/01 em Brasília.
    AS_23H59 = datetime(2030, 1, 15, 2, 59, tzinfo=dt_timezone.utc)
    # 03h00 UTC de 15/01 = 00h00 de 15/01 em Brasília.
    A_MEIA_NOITE = datetime(2030, 1, 15, 3, 0, tzinfo=dt_timezone.utc)

    def setUp(self):
        super().setUp()
        self.mensal = self.mensalidade(self.matricula(data_inicio=date(2029, 1, 1)), date(2030, 1, 1), dia=14)

    def marcar_as(self, momento):
        with patch("django.utils.timezone.now", return_value=momento):
            Mensalidade.objects.marcar_vencidas()
            self.mensal.refresh_from_db()
            return self.mensal.status, self.mensal.esta_atrasada

    def test_as_23h59_do_vencimento_ainda_nao_venceu(self):
        self.assertEqual(self.marcar_as(self.AS_23H59), ("pendente", False))

    def test_a_meia_noite_vira_vencida(self):
        self.assertEqual(self.marcar_as(self.A_MEIA_NOITE), ("vencida", True))


class PortalTests(Cenario):
    """As mesmas regras pelo cadastro do aluno no portal."""

    def setUp(self):
        super().setUp()
        self.hoje = timezone.localdate()
        usuario = get_user_model().objects.create_user("gestor", password="senha-teste-123")
        AcessoAcademia.objects.create(usuario=usuario, academia=self.academia, administrador=True)
        self.client.force_login(usuario)

    def cadastrar(self, **matricula):
        dados = {
            "nome": "Veterano", "status": "ativo", "responsavel": self.responsavel.pk,
            "matricula-modalidade": self.modalidade.pk, "matricula-valor_mensalidade": "150.00",
            "matricula-dia_vencimento": "10", "matricula-data_inicio": "2023-03-01", "matricula-ativo": "on",
        }
        dados.update({f"matricula-{campo}": valor for campo, valor in matricula.items()})
        return self.client.post(reverse("portal:novo"), dados)

    def dados_aluno(self, **extra):
        return {"nome": "Aluno", "status": "ativo", "responsavel": self.responsavel.pk, **extra}

    def test_cadastro_de_quem_ja_treina_sem_escolha_usa_o_proximo_vencimento(self):
        self.assertEqual(self.cadastrar().status_code, 302)

        mensalidade = Mensalidade.objects.get()
        self.assertGreaterEqual(mensalidade.vencimento, self.hoje)
        self.assertEqual(Matricula.objects.get().primeiro_vencimento, mensalidade.vencimento)

    def test_quem_cadastra_escolhe_o_primeiro_vencimento(self):
        escolhido = self.hoje + timedelta(days=3)

        self.assertEqual(self.cadastrar(primeiro_vencimento=escolhido.isoformat()).status_code, 302)

        self.assertEqual(Mensalidade.objects.get().vencimento, escolhido)

    def test_primeiro_vencimento_no_passado_e_recusado(self):
        resposta = self.cadastrar(primeiro_vencimento=(self.hoje - timedelta(days=1)).isoformat())

        self.assertContains(resposta, "não pode ser anterior a hoje")
        self.assertFalse(Atleta.objects.filter(nome="Veterano").exists())
        self.assertFalse(Mensalidade.objects.exists())

    def test_editar_matricula_ativa_nao_mexe_no_primeiro_vencimento(self):
        self.cadastrar()
        matricula = Matricula.objects.get()
        antes = matricula.primeiro_vencimento
        url = reverse("portal:editar_matricula", args=[matricula.atleta_id, matricula.pk])

        resposta = self.client.post(url, {
            "nome": "Veterano", "status": "ativo", "responsavel": self.responsavel.pk,
            "matricula-modalidade": self.modalidade.pk, "matricula-valor_mensalidade": "150.00",
            "matricula-dia_vencimento": "10", "matricula-data_inicio": "2023-03-01", "matricula-ativo": "on",
            "matricula-primeiro_vencimento": "2020-01-01",
        })

        self.assertEqual(resposta.status_code, 302)
        matricula.refresh_from_db()
        self.assertEqual(matricula.primeiro_vencimento, antes)
        self.assertEqual(Mensalidade.objects.count(), 1)

    def test_trancar_aluno_mantem_as_mensalidades_em_aberto(self):
        inscricao = self.matricula(data_inicio=date(2026, 1, 1))
        futura = self.mensalidade(inscricao, proximo_mes(self.hoje))

        self.client.post(reverse("portal:editar", args=[self.atleta.pk]), self.dados_aluno(status="trancado"))

        futura.refresh_from_db()
        self.assertEqual(futura.status, "pendente")

    def test_aluno_que_volta_de_trancado_e_cobrado_a_partir_do_proximo_vencimento(self):
        self.atleta.status = "trancado"
        self.atleta.save()
        inscricao = self.matricula(data_inicio=date(2023, 3, 1))

        self.client.post(reverse("portal:editar", args=[self.atleta.pk]), self.dados_aluno())

        inscricao.refresh_from_db()
        self.assertEqual(inscricao.primeiro_vencimento, proximo_vencimento(inscricao))
        self.assertGreaterEqual(Mensalidade.objects.get().vencimento, self.hoje)

    def ativar(self, inscricao, **campos):
        prefixo = f"ativar-{inscricao.pk}"
        dados = {f"{prefixo}-{campo}": valor for campo, valor in campos.items()}
        return self.client.post(reverse("portal:ativar_matricula", args=[self.atleta.pk, inscricao.pk]), dados)

    def test_administrador_define_o_vencimento_ao_ativar_no_detalhe(self):
        inscricao = self.matricula(data_inicio=date(2023, 3, 1), ativo=False)
        escolhido = self.hoje + timedelta(days=5)

        self.ativar(inscricao, dia_vencimento=15, primeiro_vencimento=escolhido.isoformat())

        inscricao.refresh_from_db()
        self.assertTrue(inscricao.ativo)
        self.assertEqual((inscricao.dia_vencimento, inscricao.primeiro_vencimento), (15, escolhido))
        self.assertEqual(Mensalidade.objects.get().vencimento, escolhido)

    def test_ativar_com_vencimento_no_passado_nao_ativa(self):
        inscricao = self.matricula(data_inicio=date(2023, 3, 1), ativo=False)

        resposta = self.ativar(
            inscricao, dia_vencimento=10, primeiro_vencimento=(self.hoje - timedelta(days=1)).isoformat(),
        )

        self.assertEqual(resposta.status_code, 302)
        inscricao.refresh_from_db()
        self.assertFalse(inscricao.ativo)
        self.assertFalse(Mensalidade.objects.exists())

    def test_detalhe_mostra_a_sugestao_de_vencimento_para_ativar(self):
        inscricao = self.matricula(data_inicio=date(2023, 3, 1), ativo=False)

        resposta = self.client.get(reverse("portal:detalhe", args=[self.atleta.pk]))

        self.assertContains(resposta, proximo_vencimento(inscricao).isoformat())
