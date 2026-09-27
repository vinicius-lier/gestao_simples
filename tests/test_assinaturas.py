"""Minha assinatura: faturas do sistema para a academia, pagas por Pix na
chave da plataforma e confirmadas no admin."""
from datetime import date, timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from academias.models import Academia
from assinaturas.models import Assinatura, FaturaAssinatura
from assinaturas.pix import br_code_estatico, crc16
from assinaturas.services import avisar_faturas_vencidas, gerar_faturas
from portal.models import AcessoAcademia

PIX = {"PLATAFORMA_PIX_CHAVE": "dev@example.com", "PLATAFORMA_PIX_NOME": "Vinicius Oliveira",
       "PLATAFORMA_PIX_CIDADE": "Volta Redonda"}


class PixEstaticoTests(TestCase):
    def test_exemplo_oficial_do_manual_do_banco_central(self):
        self.assertEqual(
            br_code_estatico("123e4567-e12b-12d1-a456-426655440000", "Fulano de Tal", "BRASILIA"),
            "00020126580014br.gov.bcb.pix0136123e4567-e12b-12d1-a456-4266554400005204000053039865802BR"
            "5913Fulano de Tal6008BRASILIA62070503***63041D3D",
        )

    def test_valor_identificador_e_codigo_verificador(self):
        codigo = br_code_estatico("dev@example.com", "Vinícius Oliveira", "Volta Redonda", Decimal("149.9"), "ASSIN000007")
        self.assertIn("5406149.90", codigo)
        self.assertIn("0511ASSIN000007", codigo)
        self.assertIn("5917Vinicius Oliveira", codigo)  # sem acento
        self.assertEqual(codigo[-4:], crc16(codigo[:-4]))


class Cenario(TestCase):
    def setUp(self):
        self.academia = Academia.objects.create(nome="Keiko", cnpj="AS1")
        self.assinatura = Assinatura.objects.create(
            academia=self.academia, valor_mensal=Decimal("149.90"), dia_vencimento=10, inicio=date(2026, 1, 1),
        )


class GerarFaturasTests(Cenario):
    def test_gera_a_fatura_do_mes_uma_vez(self):
        self.assertEqual(gerar_faturas(date(2026, 10, 3)), 1)
        self.assertEqual(gerar_faturas(date(2026, 10, 20)), 0)
        fatura = FaturaAssinatura.objects.get()
        self.assertEqual((fatura.competencia, fatura.vencimento, fatura.valor), (date(2026, 10, 1), date(2026, 10, 10), Decimal("149.90")))

    def test_respeita_o_inicio_da_cobranca(self):
        self.assinatura.inicio = date(2026, 10, 15)
        self.assinatura.save()
        self.assertEqual(gerar_faturas(date(2026, 10, 20)), 0)  # vencimento 10/10 é antes do início
        self.assertEqual(gerar_faturas(date(2026, 11, 2)), 1)

    def test_assinatura_ou_academia_inativa_nao_gera(self):
        self.assinatura.ativa = False
        self.assinatura.save()
        self.assertEqual(gerar_faturas(date(2026, 10, 3)), 0)
        self.assinatura.ativa = True
        self.assinatura.save()
        self.academia.ativo = False
        self.academia.save()
        self.assertEqual(gerar_faturas(date(2026, 10, 3)), 0)

    def test_rotina_diaria_gera_as_faturas(self):
        call_command("enviar_lembretes_cobranca", stdout=StringIO())
        self.assertTrue(FaturaAssinatura.objects.exists())

    @patch("assinaturas.services.enviar_alerta")
    def test_fatura_vencida_avisa_a_plataforma_explicando_o_que_fazer(self, alerta):
        FaturaAssinatura.objects.create(assinatura=self.assinatura, competencia=date(2026, 9, 1),
                                        valor=Decimal("149.90"), vencimento=date(2026, 9, 10))
        self.assertEqual(avisar_faturas_vencidas(date(2026, 9, 15)), 1)
        secoes = dict(alerta.call_args.kwargs["campos"])
        self.assertIn("Confirmar pagamento", secoes["O que fazer"])


class TelaTests(Cenario):
    def setUp(self):
        super().setUp()
        usuario = get_user_model().objects.create_user("dona", password="x")
        self.acesso = AcessoAcademia.objects.create(usuario=usuario, academia=self.academia, administrador=True)
        self.client.force_login(usuario)
        hoje = timezone.localdate()
        self.fatura = FaturaAssinatura.objects.create(
            assinatura=self.assinatura, competencia=hoje.replace(day=1), valor=Decimal("149.90"),
            vencimento=hoje - timedelta(days=2),
        )

    @override_settings(**PIX)
    def test_administrador_ve_o_plano_e_o_pix_da_fatura(self):
        resposta = self.client.get("/configuracoes/assinatura/")
        self.assertContains(resposta, "R$ 149,90 por mês")
        self.assertContains(resposta, "Fatura vencida")
        self.assertContains(resposta, "br.gov.bcb.pix")
        self.assertContains(resposta, "Já paguei")
        self.assertContains(resposta, "Vinicius Oliveira")

    @override_settings(PLATAFORMA_PIX_CHAVE="")
    def test_sem_chave_da_plataforma_explica_que_o_pix_nao_esta_disponivel(self):
        self.assertContains(self.client.get("/configuracoes/assinatura/"), "ainda não está disponível")

    def test_so_o_administrador_ve(self):
        self.acesso.administrador = False
        self.acesso.save()
        self.assertEqual(self.client.get("/configuracoes/assinatura/").status_code, 403)

    @patch("assinaturas.services.enviar_alerta")
    def test_ja_paguei_avisa_a_plataforma_e_aguarda_confirmacao(self, alerta):
        resposta = self.client.post(f"/configuracoes/assinatura/faturas/{self.fatura.pk}/paguei/", follow=True)
        self.fatura.refresh_from_db()
        self.assertIsNotNone(self.fatura.pagamento_informado_em)
        self.assertEqual(self.fatura.status, FaturaAssinatura.ABERTA)
        self.assertContains(resposta, "aguardando confirmação")
        self.assertIn("Pagamento informado", alerta.call_args.args[0])

    def test_fatura_de_outra_academia_nao_e_alcancada(self):
        outra = Academia.objects.create(nome="Outra", cnpj="AS2")
        alheia = FaturaAssinatura.objects.create(
            assinatura=Assinatura.objects.create(academia=outra, valor_mensal=1, inicio=date(2026, 1, 1)),
            competencia=date(2026, 1, 1), valor=1, vencimento=date(2026, 1, 10),
        )
        self.assertEqual(self.client.post(f"/configuracoes/assinatura/faturas/{alheia.pk}/paguei/").status_code, 404)

    def test_faixa_de_fatura_vencida_no_portal_ate_informar_o_pagamento(self):
        self.assertContains(self.client.get("/painel/"), "A mensalidade do sistema")
        with patch("assinaturas.services.enviar_alerta"):
            self.client.post(f"/configuracoes/assinatura/faturas/{self.fatura.pk}/paguei/")
        self.assertNotContains(self.client.get("/painel/"), "A mensalidade do sistema")

    def test_confirmar_no_admin_marca_como_paga(self):
        admin = get_user_model().objects.create_superuser("plataforma", password="x")
        self.client.force_login(admin)
        self.client.post("/admin/assinaturas/faturaassinatura/", {
            "action": "confirmar", "_selected_action": [self.fatura.pk],
        })
        self.fatura.refresh_from_db()
        self.assertEqual(self.fatura.status, FaturaAssinatura.PAGA)
        self.assertIsNotNone(self.fatura.pago_em)
