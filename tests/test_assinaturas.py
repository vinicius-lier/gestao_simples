"""Assinatura do sistema: mensalidade que a academia paga à plataforma, por
Pix (Woovi), com tolerância e suspensão. A Woovi e o Discord são simulados."""
import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from academias.models import Academia
from assinaturas.models import Assinatura, FaturaAssinatura
from assinaturas.services import (
    atualizar_situacao, confirmar_pagamento_manual, gerar_faturas, garantir_pix, registrar_pagamento_pix,
)
from financeiro.models import EventoWebhook, Repasse
from integracoes.woovi.client import Cobranca
from integracoes.woovi.exceptions import WooviError
from portal.models import AcessoAcademia
from tests.woovi_base import cobranca_criada

WOOVI = "integracoes.woovi.client.WooviClient"
ALERTA = "assinaturas.services.enviar_alerta"


class Cenario(TestCase):
    def setUp(self):
        cache.clear()
        self.academia = Academia.objects.create(
            nome="Academia Keiko Fukuda", nome_fantasia="Escola de Judô Keiko Fukuda", cnpj="24.303.368/0001-97",
        )
        self.assinatura = Assinatura.objects.create(
            academia=self.academia, valor_mensal=Decimal("150.00"), dia_vencimento=10,
            inicio=date(2026, 9, 28), primeiro_vencimento=date(2026, 11, 10), dias_tolerancia=5,
        )
        User = get_user_model()
        self.admin = User.objects.create_user("dona", password="x")
        AcessoAcademia.objects.create(usuario=self.admin, academia=self.academia, administrador=True)
        self.prof = User.objects.create_user("prof", password="x")
        AcessoAcademia.objects.create(usuario=self.prof, academia=self.academia, administrador=False)
        alerta = patch(ALERTA)
        self.alerta = alerta.start()
        self.addCleanup(alerta.stop)

    def fatura(self, vencimento, status=FaturaAssinatura.PENDENTE, **kw):
        return FaturaAssinatura.objects.create(
            assinatura=self.assinatura, competencia=vencimento.replace(day=1), valor=Decimal("150.00"),
            vencimento=vencimento, status=status, **kw,
        )

    def recarregar(self):
        self.assinatura.refresh_from_db()
        return self.assinatura


@patch(WOOVI)
class GeracaoTests(Cenario):
    def test_antes_de_01_11_nenhuma_mensalidade(self, woovi):
        for hoje in (date(2026, 9, 28), date(2026, 10, 1), date(2026, 10, 10), date(2026, 10, 31)):
            self.assertEqual(gerar_faturas(hoje), 0, hoje)
        self.assertFalse(FaturaAssinatura.objects.exists())
        woovi.return_value.criar_cobranca.assert_not_called()

    def test_em_01_11_cria_novembro_com_a_cobranca_pix(self, woovi):
        woovi.return_value.criar_cobranca.side_effect = cobranca_criada
        self.assertEqual(gerar_faturas(date(2026, 11, 1)), 1)
        fatura = FaturaAssinatura.objects.get()
        self.assertEqual((fatura.competencia, fatura.vencimento), (date(2026, 11, 1), date(2026, 11, 10)))
        self.assertEqual((fatura.valor, fatura.status), (Decimal("150.00"), FaturaAssinatura.PENDENTE))
        kwargs = woovi.return_value.criar_cobranca.call_args.kwargs
        self.assertEqual(kwargs["correlation_id"], f"assinatura-{fatura.pk}-1")
        self.assertEqual(kwargs["valor_centavos"], 15000)
        self.assertEqual(kwargs["cliente"], {"name": "Academia Keiko Fukuda", "taxID": "24303368000197"})
        self.assertEqual((fatura.correlation_id, fatura.br_code), (f"assinatura-{fatura.pk}-1", "00020126PIX"))
        self.assertIsNotNone(fatura.pix_expira_em)

    def test_idempotente_nao_duplica_mensalidade_nem_cobranca(self, woovi):
        woovi.return_value.criar_cobranca.side_effect = cobranca_criada
        for hoje in (date(2026, 11, 1), date(2026, 11, 1), date(2026, 11, 5)):
            gerar_faturas(hoje)
        self.assertEqual(FaturaAssinatura.objects.count(), 1)
        woovi.return_value.criar_cobranca.assert_called_once()

    def test_competencia_mensal_com_dia_de_vencimento(self, woovi):
        woovi.return_value.criar_cobranca.side_effect = cobranca_criada
        for hoje in (date(2026, 11, 1), date(2026, 12, 1), date(2027, 1, 1), date(2027, 2, 1)):
            gerar_faturas(hoje)
        self.assertEqual(
            list(FaturaAssinatura.objects.order_by("vencimento").values_list("vencimento", flat=True)),
            [date(2026, 11, 10), date(2026, 12, 10), date(2027, 1, 10), date(2027, 2, 10)],
        )

    def test_valor_vem_da_assinatura(self, woovi):
        woovi.return_value.criar_cobranca.side_effect = cobranca_criada
        self.assinatura.valor_mensal = Decimal("180.00")
        self.assinatura.save()
        gerar_faturas(date(2026, 11, 1))
        self.assertEqual(FaturaAssinatura.objects.get().valor, Decimal("180.00"))
        self.assertEqual(woovi.return_value.criar_cobranca.call_args.kwargs["valor_centavos"], 18000)

    def test_assinatura_cancelada_ou_academia_inativa_nao_gera(self, woovi):
        self.assinatura.status = Assinatura.CANCELADA
        self.assinatura.save()
        self.assertEqual(gerar_faturas(date(2026, 11, 1)), 0)
        self.assinatura.status = Assinatura.ATIVA
        self.assinatura.save()
        self.academia.ativo = False
        self.academia.save()
        self.assertEqual(gerar_faturas(date(2026, 11, 1)), 0)

    def test_falha_da_woovi_nao_impede_a_mensalidade(self, woovi):
        woovi.return_value.criar_cobranca.side_effect = WooviError("fora do ar")
        self.assertEqual(gerar_faturas(date(2026, 11, 1)), 1)
        self.assertEqual(FaturaAssinatura.objects.get().br_code, "")

    def test_pix_vencido_e_trocado_por_outro_sem_repetir_o_id(self, woovi):
        woovi.return_value.criar_cobranca.side_effect = cobranca_criada
        fatura = self.fatura(date(2026, 11, 10), correlation_id="x", br_code="velho",
                             pix_expira_em=timezone.now() - timedelta(minutes=1))
        fatura.correlation_id = f"assinatura-{fatura.pk}-1"
        fatura.save()
        fatura = garantir_pix(fatura)
        self.assertEqual(fatura.correlation_id, f"assinatura-{fatura.pk}-2")
        self.assertEqual(fatura.br_code, "00020126PIX")

    def test_sem_pix_para_fatura_paga(self, woovi):
        with self.assertRaises(ValueError):
            garantir_pix(self.fatura(date(2026, 11, 10), status=FaturaAssinatura.PAGA))
        woovi.return_value.criar_cobranca.assert_not_called()


class CenarioObrigatorioTests(Cenario):
    """10/11 vencimento · 11/11 atrasada · até 15/11 tolerância · 16/11 suspensa · Pix pago reativa."""

    def test_linha_do_tempo_completa(self):
        with patch(WOOVI) as woovi:
            woovi.return_value.criar_cobranca.side_effect = cobranca_criada
            gerar_faturas(date(2026, 11, 1))
        fatura = FaturaAssinatura.objects.get()

        atualizar_situacao(self.assinatura, date(2026, 11, 10))
        fatura.refresh_from_db()
        self.assertEqual((fatura.status, self.recarregar().status), (FaturaAssinatura.PENDENTE, Assinatura.ATIVA))

        atualizar_situacao(self.assinatura, date(2026, 11, 11))
        fatura.refresh_from_db()
        self.assertEqual((fatura.status, self.recarregar().status), (FaturaAssinatura.ATRASADA, Assinatura.ATIVA))

        atualizar_situacao(self.assinatura, date(2026, 11, 15))
        self.assertEqual(self.recarregar().status, Assinatura.ATIVA)

        atualizar_situacao(self.assinatura, date(2026, 11, 16))
        assinatura = self.recarregar()
        self.assertEqual((assinatura.status, assinatura.motivo_suspensao), (Assinatura.SUSPENSA, Assinatura.INADIMPLENCIA))
        self.assertIn("suspensa", self.alerta.call_args.args[0].lower())

        # Pagamento confirmado pela Woovi (webhook).
        with patch("integracoes.woovi.views.assinatura_valida", return_value=True), patch("integracoes.woovi.views.WooviClient") as origem:
            origem.return_value.obter_cobranca.return_value = Cobranca(
                fatura.correlation_id, "COMPLETED", 15000, "", "", None, "tx-123", None,
            )
            resp = self.client.post("/webhooks/woovi/", data=json.dumps({
                "event": "OPENPIX:CHARGE_COMPLETED",
                "charge": {"correlationID": fatura.correlation_id, "status": "COMPLETED", "value": 15000,
                           "transactionID": "tx-123", "paidAt": "2026-11-16T15:00:00Z"},
                "pix": {"endToEndId": "E123"},
            }), content_type="application/json", HTTP_X_WEBHOOK_SIGNATURE="ok")
        self.assertEqual(resp.status_code, 200)
        fatura.refresh_from_db()
        self.assertEqual(fatura.status, FaturaAssinatura.PAGA)
        self.assertEqual(fatura.pago_em, datetime(2026, 11, 16, 15, 0, tzinfo=timezone.UTC))
        self.assertEqual(fatura.woovi_charge_id, "tx-123")
        assinatura = self.recarregar()
        self.assertEqual((assinatura.status, assinatura.motivo_suspensao), (Assinatura.ATIVA, ""))
        # O dinheiro é da plataforma: nenhum repasse para a academia.
        self.assertFalse(Repasse.objects.exists())

        # Acesso recuperado automaticamente.
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get("/alunos/").status_code, 200)


class WebhookTests(Cenario):
    def enviar(self, correlation_id, transacao="tx-1", e2e="E1"):
        with patch("integracoes.woovi.views.assinatura_valida", return_value=True), patch("integracoes.woovi.views.WooviClient") as origem:
            origem.return_value.obter_cobranca.return_value = Cobranca(
                correlation_id, "COMPLETED", 15000, "", "", None, transacao, None,
            )
            return self.client.post("/webhooks/woovi/", data=json.dumps({
                "event": "OPENPIX:CHARGE_COMPLETED",
                "charge": {"correlationID": correlation_id, "status": "COMPLETED", "value": 15000,
                           "transactionID": transacao, "paidAt": "2026-11-12T10:00:00Z"},
                "pix": {"endToEndId": e2e},
            }), content_type="application/json", HTTP_X_WEBHOOK_SIGNATURE="ok")

    def test_webhook_repetido_nao_processa_de_novo(self):
        fatura = self.fatura(date(2026, 11, 10), correlation_id="")
        fatura.correlation_id = f"assinatura-{fatura.pk}-1"
        fatura.save()
        self.enviar(fatura.correlation_id)
        pago_em = FaturaAssinatura.objects.get().pago_em
        self.assertTrue(self.enviar(fatura.correlation_id).json().get("duplicado"))
        self.assertEqual(FaturaAssinatura.objects.get().pago_em, pago_em)
        self.assertEqual(EventoWebhook.objects.count(), 1)

    def test_pix_antigo_da_mesma_fatura_tambem_da_baixa(self):
        fatura = self.fatura(date(2026, 11, 10))
        fatura.correlation_id = f"assinatura-{fatura.pk}-2"
        fatura.save()
        self.enviar(f"assinatura-{fatura.pk}-1")
        fatura.refresh_from_db()
        self.assertEqual(fatura.status, FaturaAssinatura.PAGA)

    def test_fatura_inexistente_fica_registrada_sem_efeito(self):
        self.assertEqual(self.enviar("assinatura-999-1").status_code, 200)
        evento = EventoWebhook.objects.get()
        self.assertEqual((evento.status, evento.erro), (EventoWebhook.IGNORADO, "fatura da assinatura não encontrada"))

    def test_sem_assinatura_valida_da_woovi_e_recusado(self):
        fatura = self.fatura(date(2026, 11, 10))
        resp = self.client.post("/webhooks/woovi/", data=json.dumps({
            "event": "OPENPIX:CHARGE_COMPLETED",
            "charge": {"correlationID": f"assinatura-{fatura.pk}-1", "value": 15000},
        }), content_type="application/json", HTTP_X_WEBHOOK_SIGNATURE="falsa")
        self.assertEqual(resp.status_code, 401)
        fatura.refresh_from_db()
        self.assertEqual(fatura.status, FaturaAssinatura.PENDENTE)

    def test_suspensa_manualmente_nao_e_reativada_pelo_pagamento(self):
        fatura = self.fatura(date(2026, 11, 10))
        self.assinatura.status = Assinatura.SUSPENSA
        self.assinatura.motivo_suspensao = Assinatura.MANUAL
        self.assinatura.save()
        registrar_pagamento_pix(f"assinatura-{fatura.pk}-1")
        self.assertEqual(self.recarregar().status, Assinatura.SUSPENSA)


class AcessoTests(Cenario):
    """Com datas reais: a suspensão é conferida a cada acesso ao painel."""

    def suspender(self):
        fatura = self.fatura(timezone.localdate() - timedelta(days=6))
        atualizar_situacao(self.assinatura)
        self.assertEqual(self.recarregar().status, Assinatura.SUSPENSA)
        return fatura

    def test_dentro_da_tolerancia_acesso_normal_com_aviso(self):
        self.fatura(timezone.localdate() - timedelta(days=3))
        self.client.force_login(self.admin)
        resp = self.client.get("/alunos/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "O acesso continua normal até")
        self.assertEqual(self.recarregar().status, Assinatura.ATIVA)

    def test_suspensa_bloqueia_o_painel_mas_deixa_pagar(self):
        self.suspender()
        self.client.force_login(self.admin)
        self.assertRedirects(self.client.get("/alunos/"), "/configuracoes/assinatura/")
        self.assertRedirects(self.client.get("/financeiro/"), "/configuracoes/assinatura/")
        pagina = self.client.get("/configuracoes/assinatura/")
        self.assertContains(pagina, "Acesso suspenso")
        with patch(WOOVI) as woovi:
            woovi.return_value.criar_cobranca.side_effect = cobranca_criada
            self.assertContains(self.client.get("/configuracoes/assinatura/pagar/"), "Copiar código Pix")

    def test_professor_ve_a_tela_de_suspensao(self):
        self.suspender()
        self.client.force_login(self.prof)
        resp = self.client.get("/alunos/")
        self.assertContains(resp, "O painel da escola está suspenso", status_code=403)

    def test_login_continua_funcionando(self):
        self.suspender()
        self.admin.set_password("senha-teste-123")
        self.admin.save()
        resp = self.client.post("/login/", {"username": "dona", "password": "senha-teste-123"})
        self.assertEqual(resp.status_code, 302)

    def test_suspensao_nao_apaga_nada(self):
        from atletas.models import Atleta
        Atleta.objects.create(academia=self.academia, nome="Aluno")
        self.suspender()
        self.assertTrue(Atleta.objects.exists() and FaturaAssinatura.objects.exists())

    def test_baixa_manual_no_admin_reativa(self):
        fatura = self.suspender()
        self.assertTrue(confirmar_pagamento_manual(fatura))
        self.assertEqual(self.recarregar().status, Assinatura.ATIVA)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get("/alunos/").status_code, 200)


class TelaTests(Cenario):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)

    def test_minha_assinatura_mostra_valor_status_e_proximo_vencimento(self):
        FaturaAssinatura.objects.create(
            assinatura=self.assinatura, tipo=FaturaAssinatura.IMPLANTACAO, competencia=date(2026, 9, 1),
            valor=Decimal("1200.00"), vencimento=date(2026, 9, 28), status=FaturaAssinatura.PAGA,
            pago_em=timezone.make_aware(datetime(2026, 9, 28, 12, 0)),
        )
        resp = self.client.get("/configuracoes/assinatura/")
        for trecho in ("Mensalidade do sistema", "R$ 150,00", "/ mês", "Ativa", "Pagamento via Pix",
                       "Todo dia 10", "Histórico de pagamentos", "Implantação", "R$ 1.200,00", "Pago em 28/09/2026"):
            self.assertContains(resp, trecho)
        if timezone.localdate() < date(2026, 11, 10):
            self.assertContains(resp, "10/11/2026")
        self.assertNotContains(resp, "Woovi")

    def test_pagar_mostra_qr_code_e_copia_e_cola_com_o_valor_do_banco(self):
        fatura = self.fatura(timezone.localdate() + timedelta(days=5))
        with patch(WOOVI) as woovi:
            woovi.return_value.criar_cobranca.side_effect = cobranca_criada
            resp = self.client.get("/configuracoes/assinatura/pagar/?valor=1")
        self.assertContains(resp, "pix-qrcode")
        self.assertContains(resp, "00020126PIX")
        self.assertContains(resp, "Aguardando pagamento")
        self.assertEqual(woovi.return_value.criar_cobranca.call_args.kwargs["valor_centavos"], 15000)
        self.assertNotContains(resp, "Woovi")
        fatura.refresh_from_db()
        self.assertTrue(fatura.pix_vigente)

    def test_status_confere_na_woovi_quando_o_webhook_atrasa(self):
        fatura = self.fatura(timezone.localdate() + timedelta(days=5))
        fatura.correlation_id = f"assinatura-{fatura.pk}-1"
        fatura.save()
        with patch(WOOVI) as woovi:
            woovi.return_value.obter_cobranca.return_value = Cobranca(
                correlation_id=fatura.correlation_id, status="COMPLETED", valor_centavos=15000, br_code="x",
                link_pagamento="", expira_em=None, transaction_id="tx-9", pago_em=timezone.now(),
            )
            dados = self.client.get(f"/configuracoes/assinatura/faturas/{fatura.pk}/status/").json()
            # Consultas seguidas não voltam à Woovi.
            self.client.get(f"/configuracoes/assinatura/faturas/{fatura.pk}/status/")
        self.assertTrue(dados["paga"])
        woovi.return_value.obter_cobranca.assert_called_once()

    def test_so_administrador_e_so_a_propria_academia(self):
        fatura = self.fatura(timezone.localdate() + timedelta(days=5))
        outra = Academia.objects.create(nome="Outra", cnpj="99")
        usuario = get_user_model().objects.create_user("outro", password="x")
        AcessoAcademia.objects.create(usuario=usuario, academia=outra, administrador=True)
        self.client.force_login(usuario)
        self.assertEqual(self.client.get(f"/configuracoes/assinatura/faturas/{fatura.pk}/status/").status_code, 404)
        self.client.force_login(self.prof)
        self.assertEqual(self.client.get("/configuracoes/assinatura/").status_code, 403)
        self.assertEqual(self.client.get("/configuracoes/assinatura/pagar/").status_code, 403)

    def test_sem_assinatura(self):
        self.assinatura.delete()
        self.assertContains(self.client.get("/configuracoes/assinatura/"), "Ainda não há uma assinatura")


class RotinaDiariaTests(Cenario):
    def test_rotina_diaria_chama_a_assinatura(self):
        with patch("assinaturas.services.rotina_diaria", return_value=0) as rotina:
            call_command("enviar_lembretes_cobranca", stdout=StringIO())
        rotina.assert_called_once()
