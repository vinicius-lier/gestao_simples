from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

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

    @patch("integracoes.woovi.services.WooviClient")
    def test_gerar_pix_na_woovi(self, mock_client_class):
        mock_client_class.return_value.criar_cobranca.side_effect = lambda **kw: {
            "correlationID": kw["correlation_id"],
            "brCode": "00020126...copia-e-cola",
            "qrCodeImage": "https://api.woovi.com/openpix/charge/brcode/image/x.png",
            "paymentLinkUrl": "https://woovi.com/pay/x",
            "expiresDate": "2999-01-01T00:00:00.000Z",
        }

        resposta = self.client.post(f"/financeiro/cobrancas/{self.pendente.pk}/pix/", {})

        self.assertEqual(resposta.status_code, 302)
        self.pendente.refresh_from_db()
        self.assertTrue(self.pendente.woovi_correlation_id.startswith(f"mensalidade-{self.pendente.pk}-"))
        self.assertEqual(self.pendente.woovi_br_code, "00020126...copia-e-cola")
        self.assertTrue(self.pendente.pix_vigente)
        kwargs = mock_client_class.return_value.criar_cobranca.call_args.kwargs
        self.assertEqual(kwargs["valor_centavos"], 12000)
        self.assertEqual(kwargs["cliente"]["name"], "Resp")

    @override_settings(WOOVI_APP_ID="")
    def test_gerar_pix_sem_woovi_configurada_mostra_erro_sem_quebrar(self):
        resposta = self.client.post(f"/financeiro/cobrancas/{self.pendente.pk}/pix/", {}, follow=True)
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Não foi possível gerar o Pix: WOOVI_APP_ID não configurado.")

    def test_lista_cobrancas_mostra_link_do_qrcode_so_com_pix_vigente(self):
        html = self.client.get("/financeiro/cobrancas/").content.decode("utf8")
        self.assertNotIn("Ver Pix", html)
        self.assertIn("Gerar Pix", html)

        self.pendente.woovi_correlation_id = "mensalidade-abc"
        self.pendente.woovi_expira_em = timezone.now() + timedelta(days=10)
        self.pendente.save(update_fields=["woovi_correlation_id", "woovi_expira_em"])
        html = self.client.get("/financeiro/cobrancas/").content.decode("utf8")
        self.assertIn("Ver Pix", html)
        self.assertIn(f"/financeiro/cobrancas/{self.pendente.pk}/pix/qrcode/", html)

    def test_pix_expirado_volta_a_oferecer_gerar_pix(self):
        self.pendente.woovi_correlation_id = "mensalidade-abc"
        self.pendente.woovi_expira_em = timezone.now() - timedelta(minutes=1)
        self.pendente.save(update_fields=["woovi_correlation_id", "woovi_expira_em"])
        html = self.client.get("/financeiro/cobrancas/").content.decode("utf8")
        self.assertNotIn(f"/financeiro/cobrancas/{self.pendente.pk}/pix/qrcode/", html)

    @patch("integracoes.woovi.services.WooviClient")
    def test_tela_do_pix_mostra_qrcode_e_copia_e_cola_sem_chamar_a_woovi(self, mock_client_class):
        self.pendente.woovi_correlation_id = "mensalidade-abc"
        self.pendente.woovi_br_code = "00020126...copia-e-cola"
        self.pendente.woovi_qrcode_url = "https://api.woovi.com/openpix/charge/brcode/image/x.png"
        self.pendente.woovi_link_pagamento = "https://woovi.com/pay/x"
        self.pendente.woovi_expira_em = timezone.now() + timedelta(days=10)
        self.pendente.save()

        resposta = self.client.get(f"/financeiro/cobrancas/{self.pendente.pk}/pix/qrcode/")

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "00020126...copia-e-cola")
        self.assertContains(resposta, "https://api.woovi.com/openpix/charge/brcode/image/x.png")
        self.assertContains(resposta, "https://woovi.com/pay/x")
        self.assertContains(resposta, "detalhe-conteudo")
        mock_client_class.assert_not_called()

    def test_tela_do_pix_sem_cobranca_mostra_mensagem_amigavel(self):
        resposta = self.client.get(f"/financeiro/cobrancas/{self.pendente.pk}/pix/qrcode/")
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "não tem um Pix vigente")

    @patch("integracoes.woovi.services.WooviClient")
    def test_marcar_pago_tira_o_pix_do_ar(self, mock_client_class):
        self.pendente.woovi_correlation_id = "mensalidade-abc"
        self.pendente.woovi_expira_em = timezone.now() + timedelta(days=10)
        self.pendente.save(update_fields=["woovi_correlation_id", "woovi_expira_em"])

        self.client.post(f"/financeiro/cobrancas/{self.pendente.pk}/pagar/", {"forma_pagamento": "dinheiro"})

        mock_client_class.return_value.remover_cobranca.assert_called_once_with("mensalidade-abc")
        self.pendente.refresh_from_db()
        self.assertEqual(self.pendente.status, "paga")

    @patch("integracoes.woovi.services.WooviClient")
    def test_marcar_pago_avisa_se_nao_conseguir_tirar_o_pix_do_ar(self, mock_client_class):
        from integracoes.woovi.client import WooviAPIError

        mock_client_class.return_value.remover_cobranca.side_effect = WooviAPIError("fora do ar")
        self.pendente.woovi_correlation_id = "mensalidade-abc"
        self.pendente.woovi_expira_em = timezone.now() + timedelta(days=10)
        self.pendente.save(update_fields=["woovi_correlation_id", "woovi_expira_em"])

        resposta = self.client.post(
            f"/financeiro/cobrancas/{self.pendente.pk}/pagar/", {"forma_pagamento": "dinheiro"}, follow=True
        )

        self.assertContains(resposta, "pagamento em dobro")
        self.pendente.refresh_from_db()
        self.assertEqual(self.pendente.status, "paga")

    def test_tela_do_pix_isola_por_academia(self):
        self.assertEqual(
            self.client.get(f"/financeiro/cobrancas/{self.mensalidade_b.pk}/pix/qrcode/").status_code, 404
        )

    @patch("integracoes.whatsapp.services.WhatsAppClient")
    def test_enviar_cobranca_com_whatsapp_configurado(self, mock_client_class):
        from portal.models import TokenAcessoResponsavel

        mock_client_class.return_value.enviar_template.return_value = {"ok": True}

        resposta = self.client.post(f"/financeiro/cobrancas/{self.pendente.pk}/enviar/", follow=True)

        self.assertContains(resposta, "enviada automaticamente")
        kwargs = mock_client_class.return_value.enviar_template.call_args.kwargs
        link_enviado = kwargs["parametros"][-1]
        self.assertIn(f"/responsavel/mensalidade/{self.pendente.pk}/pagar/", link_enviado)
        self.assertEqual(TokenAcessoResponsavel.objects.count(), 1)

    def test_enviar_cobranca_sem_whatsapp_mostra_link_manual(self):
        resposta = self.client.post(f"/financeiro/cobrancas/{self.pendente.pk}/enviar/")
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "/responsavel/entrar/")

    def test_enviar_cobranca_isola_por_academia(self):
        resposta = self.client.post(f"/financeiro/cobrancas/{self.mensalidade_b.pk}/enviar/")
        self.assertEqual(resposta.status_code, 404)


class EncerrarCobrancaTests(TestCase):
    """Cancelar/isentar mensalidade pelo portal — só administrador."""

    def setUp(self):
        self.a = Academia.objects.create(nome="A", cnpj="EC1")
        self.b = Academia.objects.create(nome="B", cnpj="EC2")
        self.admin = get_user_model().objects.create_user("admin", password="senha123")
        AcessoAcademia.objects.create(usuario=self.admin, academia=self.a, administrador=True)
        self.operador = get_user_model().objects.create_user("operador", password="senha123")
        AcessoAcademia.objects.create(usuario=self.operador, academia=self.a)
        atleta = Atleta.objects.create(academia=self.a, nome="João da Silva")
        self.matricula = Matricula.objects.create(
            academia=self.a, atleta=atleta, modalidade=Modalidade.objects.create(academia=self.a, nome="Judô"),
            valor_mensalidade=Decimal("120.00"), dia_vencimento=10, data_inicio=date(2026, 1, 1),
        )
        self.mensalidade = self.criar(date(2999, 1, 1))
        self.client.force_login(self.admin)

    def criar(self, competencia, **extra):
        return Mensalidade.objects.create(
            academia=self.a, matricula=self.matricula, competencia=competencia,
            valor=Decimal("120.00"), vencimento=competencia.replace(day=10), status="pendente", **extra,
        )

    def encerrar(self, mensalidade, status):
        return self.client.post(
            f"/financeiro/cobrancas/{mensalidade.pk}/encerrar/", {"status": status}, follow=True
        )

    def test_admin_cancela_mensalidade_sem_pix(self):
        with patch("integracoes.woovi.services.WooviClient") as mock_client_class:
            resposta = self.encerrar(self.mensalidade, "cancelada")
        self.assertContains(resposta, "cancelada")
        mock_client_class.assert_not_called()
        self.mensalidade.refresh_from_db()
        self.assertEqual(self.mensalidade.status, "cancelada")

    def test_admin_isenta_mensalidade(self):
        self.encerrar(self.mensalidade, "isenta")
        self.mensalidade.refresh_from_db()
        self.assertEqual(self.mensalidade.status, "isenta")

    def criar_com_pix(self, competencia, expira_em):
        return self.criar(competencia, woovi_correlation_id="mensalidade-123", woovi_expira_em=expira_em)

    @patch("integracoes.woovi.services.WooviClient")
    def test_exclui_o_pix_na_woovi_antes_de_cancelar(self, mock_client_class):
        mensalidade = self.criar_com_pix(date(2999, 2, 1), timezone.now() + timedelta(days=10))
        self.encerrar(mensalidade, "cancelada")
        mock_client_class.return_value.remover_cobranca.assert_called_once_with("mensalidade-123")
        mensalidade.refresh_from_db()
        self.assertEqual(mensalidade.status, "cancelada")

    @patch("integracoes.woovi.services.WooviClient")
    def test_pix_ja_expirado_nao_precisa_ser_excluido(self, mock_client_class):
        mensalidade = self.criar_com_pix(date(2999, 2, 1), timezone.now() - timedelta(minutes=1))
        self.encerrar(mensalidade, "cancelada")
        mock_client_class.assert_not_called()
        mensalidade.refresh_from_db()
        self.assertEqual(mensalidade.status, "cancelada")

    @patch("integracoes.woovi.services.WooviClient")
    def test_falha_na_woovi_nao_cancela_localmente(self, mock_client_class):
        from integracoes.woovi.client import WooviAPIError

        mock_client_class.return_value.remover_cobranca.side_effect = WooviAPIError(
            "A API da Woovi retornou o status HTTP 400."
        )
        mensalidade = self.criar_com_pix(date(2999, 2, 1), timezone.now() + timedelta(days=10))
        resposta = self.encerrar(mensalidade, "cancelada")
        self.assertContains(resposta, "Não foi possível alterar a mensalidade")
        mensalidade.refresh_from_db()
        self.assertEqual(mensalidade.status, "pendente")

    def test_mensalidade_paga_nao_pode_ser_cancelada(self):
        self.mensalidade.status = "paga"
        self.mensalidade.save(update_fields=["status"])
        resposta = self.encerrar(self.mensalidade, "cancelada")
        self.assertContains(resposta, "Só mensalidades em aberto")
        self.mensalidade.refresh_from_db()
        self.assertEqual(self.mensalidade.status, "paga")

    def test_status_invalido_e_recusado(self):
        self.encerrar(self.mensalidade, "paga")
        self.mensalidade.refresh_from_db()
        self.assertEqual(self.mensalidade.status, "pendente")

    def test_operador_sem_administrador_recebe_403(self):
        self.client.force_login(self.operador)
        resposta = self.client.post(
            f"/financeiro/cobrancas/{self.mensalidade.pk}/encerrar/", {"status": "cancelada"}
        )
        self.assertEqual(resposta.status_code, 403)
        self.mensalidade.refresh_from_db()
        self.assertEqual(self.mensalidade.status, "pendente")

    def test_botao_aparece_so_para_administrador(self):
        url_acao = f"/financeiro/cobrancas/{self.mensalidade.pk}/encerrar/"
        self.assertContains(self.client.get("/financeiro/cobrancas/"), url_acao)
        self.client.force_login(self.operador)
        self.assertNotContains(self.client.get("/financeiro/cobrancas/"), url_acao)

    def test_isola_por_academia(self):
        atleta_b = Atleta.objects.create(academia=self.b, nome="Secreto")
        matricula_b = Matricula.objects.create(
            academia=self.b, atleta=atleta_b, modalidade=Modalidade.objects.create(academia=self.b, nome="Karatê"),
            valor_mensalidade=Decimal("50.00"), dia_vencimento=10, data_inicio=date(2026, 1, 1),
        )
        mensalidade_b = Mensalidade.objects.create(
            academia=self.b, matricula=matricula_b, competencia=date(2026, 9, 1),
            valor=Decimal("50.00"), vencimento=date(2026, 9, 10), status="pendente",
        )
        resposta = self.client.post(f"/financeiro/cobrancas/{mensalidade_b.pk}/encerrar/", {"status": "cancelada"})
        self.assertEqual(resposta.status_code, 404)
