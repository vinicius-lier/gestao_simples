from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from academias.models import Academia
from atletas.models import Atleta, Responsavel
from financeiro.models import Mensalidade
from matriculas.models import Matricula
from modalidades.models import Modalidade
from portal.models import AcessoAcademia


class FinanceiroPortalTests(TestCase):
    def setUp(self):
        self.a = Academia.objects.create(nome="A", cnpj="FA1")
        self.b = Academia.objects.create(nome="B", cnpj="FB1")
        self.user = get_user_model().objects.create_user("gestor", password="senha123")
        AcessoAcademia.objects.create(usuario=self.user, academia=self.a)
        self.responsavel = Responsavel.objects.create(
            academia=self.a, nome="Resp", cpf="1", whatsapp="21999998888"
        )
        self.atleta = Atleta.objects.create(
            academia=self.a, nome="João da Silva", responsavel_financeiro=self.responsavel
        )
        self.modalidade = Modalidade.objects.create(academia=self.a, nome="Judô")
        self.matricula = Matricula.objects.create(
            academia=self.a, atleta=self.atleta, modalidade=self.modalidade,
            valor_mensalidade=Decimal("120.00"), dia_vencimento=10, data_inicio=date(2026, 1, 1),
        )
        self.pendente = Mensalidade.objects.create(
            academia=self.a, matricula=self.matricula, competencia=date(2026, 9, 1),
            valor=Decimal("120.00"), vencimento=date(2999, 1, 1), status="pendente",
        )
        self.vencida = Mensalidade.objects.create(
            academia=self.a, matricula=self.matricula, competencia=date(2026, 8, 1),
            valor=Decimal("120.00"), vencimento=date(2020, 1, 1), status="pendente",
        )
        atleta_b = Atleta.objects.create(academia=self.b, nome="Secreto")
        matricula_b = Matricula.objects.create(
            academia=self.b, atleta=atleta_b,
            modalidade=Modalidade.objects.create(academia=self.b, nome="Karatê"),
            valor_mensalidade=Decimal("50.00"), dia_vencimento=10, data_inicio=date(2026, 1, 1),
        )
        self.mensalidade_b = Mensalidade.objects.create(
            academia=self.b, matricula=matricula_b, competencia=date(2026, 9, 1),
            valor=Decimal("50.00"), vencimento=date(2026, 9, 10), status="pendente",
        )
        self.client.force_login(self.user)

    def test_dashboard_mostra_indicadores_e_marca_vencidas(self):
        response = self.client.get("/financeiro/")
        self.assertEqual(response.status_code, 200)
        self.vencida.refresh_from_db()
        self.assertEqual(self.vencida.status, "vencida")
        self.assertContains(response, "João da Silva")
        self.assertNotContains(response, "Secreto")

    def test_lista_cobrancas_isola_por_academia_e_filtra(self):
        response = self.client.get("/financeiro/cobrancas/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "João da Silva")
        self.assertNotContains(response, "Secreto")

        busca = self.client.get("/financeiro/cobrancas/", {"q": "joão"})
        self.assertContains(busca, "João da Silva")

        sem_resultado = self.client.get("/financeiro/cobrancas/", {"q": "ninguem"})
        self.assertNotContains(sem_resultado, "João da Silva")

        por_mes = self.client.get("/financeiro/cobrancas/", {"mes": "2026-08"})
        self.assertContains(por_mes, "08/2026")
        self.assertNotContains(por_mes, "09/2026")

    def test_marcar_pago_atualiza_status_e_redireciona(self):
        resposta = self.client.post(
            f"/financeiro/cobrancas/{self.pendente.pk}/pagar/",
            {"forma_pagamento": "dinheiro", "proximo": "/financeiro/cobrancas/"},
        )
        self.assertRedirects(resposta, "/financeiro/cobrancas/")
        self.pendente.refresh_from_db()
        self.assertEqual(self.pendente.status, "paga")
        self.assertEqual(self.pendente.forma_pagamento, "dinheiro")

    def test_nao_marca_pago_mensalidade_de_outra_academia(self):
        resposta = self.client.post(f"/financeiro/cobrancas/{self.mensalidade_b.pk}/pagar/", {})
        self.assertEqual(resposta.status_code, 404)
        self.mensalidade_b.refresh_from_db()
        self.assertEqual(self.mensalidade_b.status, "pendente")

    def test_marcar_pago_via_get_nao_e_permitido(self):
        self.assertEqual(
            self.client.get(f"/financeiro/cobrancas/{self.pendente.pk}/pagar/").status_code, 405
        )

    def test_proximo_externo_e_ignorado(self):
        resposta = self.client.post(
            f"/financeiro/cobrancas/{self.pendente.pk}/pagar/",
            {"proximo": "https://evil.example.com/"},
        )
        self.assertRedirects(resposta, "/financeiro/cobrancas/")

    @patch("integracoes.asaas.services.AsaasClient")
    @patch("integracoes.asaas.services.sincronizar_responsavel_asaas")
    def test_gerar_pix_cria_cobranca_no_asaas(self, mock_sincronizar, mock_client_class):
        mock_sincronizar.return_value = "cus_123"
        mock_client_class.return_value.criar_cobranca.return_value = {"id": "pay_xyz"}

        resposta = self.client.post(f"/financeiro/cobrancas/{self.pendente.pk}/pix/", {})

        self.assertEqual(resposta.status_code, 302)
        self.pendente.refresh_from_db()
        self.assertEqual(self.pendente.asaas_payment_id, "pay_xyz")

    def test_gerar_pix_sem_responsavel_mostra_erro_sem_quebrar(self):
        self.atleta.responsavel_financeiro = None
        self.atleta.save(update_fields=["responsavel_financeiro"])
        resposta = self.client.post(f"/financeiro/cobrancas/{self.pendente.pk}/pix/", {}, follow=True)
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Não foi possível gerar a cobrança Pix")
