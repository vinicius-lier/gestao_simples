"""Isolamento financeiro e migração gradual, sem rede real."""
import json
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import Mock, patch

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone

from assinaturas.models import Assinatura, FaturaAssinatura
from assinaturas.services import garantir_pix
from financeiro.models import CobrancaPix, ContaRecebimento, EventoWebhook, Repasse
from integracoes.woovi.client import Cobranca, WooviClient
from integracoes.woovi.contas import ativar_conta, iniciar_onboarding, preparar_conta
from integracoes.woovi.credenciais import fingerprint, referencia_academia
from integracoes.woovi.exceptions import WooviAuthError, WooviConfigError, WooviInvalidResponseError, WooviUnavailableError
from integracoes.woovi.services import (
    RecebimentoNaoConfigurado, conferir_pagamento_pix, garantir_cobranca_pix,
    registrar_pagamento_pix, remover_cobranca_pix, solicitar_repasse,
)
from tests.test_woovi_assinatura import assinar, gerar_chave
from tests.test_woovi_webhook import pago
from tests.woovi_base import CenarioWoovi

PRIVADA, PEM = gerar_chave()
SEGREDO = "segredo-exclusivo-academia-teste"


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class ContaPropriaTests(CenarioWoovi, TestCase):
    def setUp(self):
        self.criar_cenario()
        self.legada = self.conta
        self.academia.cnpj = "12345678000195"
        self.academia.save(update_fields=["cnpj"])
        self.settings_conta = override_settings(**{referencia_academia(self.academia.pk): SEGREDO})
        self.settings_conta.enable()
        self.addCleanup(self.settings_conta.disable)

    def http(self, dados, status=200):
        return Mock(status_code=status, json=Mock(return_value=dados))

    @patch("integracoes.woovi.client.requests.request")
    def test_nova_cobranca_envia_credencial_exclusiva_sem_subconta(self, request):
        self.usar_conta_propria()
        def resposta(metodo, url, **kw):
            body = kw["json"]
            return self.http({"charge": {**body, "status": "ACTIVE", "brCode": "PIX-NOVO",
                "paymentLinkUrl": "https://woovi.com/pay/novo", "transactionID": "tx-novo"}})
        request.side_effect = resposta
        charge = garantir_cobranca_pix(self.mensalidade)
        kw = request.call_args.kwargs
        self.assertEqual(kw["headers"]["Authorization"], SEGREDO)
        self.assertFalse(kw["allow_redirects"])
        self.assertNotIn("subaccount", json.dumps(kw["json"]).lower())
        self.assertNotIn("splits", kw["json"])
        self.assertEqual(charge.modelo_recebimento, ContaRecebimento.CONTA_PROPRIA)
        self.assertEqual((charge.transaction_id, charge.br_code, charge.link_pagamento),
                         ("tx-novo", "PIX-NOVO", "https://woovi.com/pay/novo"))
        self.assertFalse(Repasse.objects.exists())

    @patch("integracoes.woovi.services.WooviClient")
    def test_legado_vigente_preservado_mesmo_perto_de_expirar_e_valor_alterado(self, client):
        antiga = self.cobranca()
        antiga.expira_em = timezone.now() + timedelta(minutes=1)
        antiga.save()
        self.usar_conta_propria()
        self.mensalidade.valor = Decimal("999.00")
        self.mensalidade.save()
        self.assertEqual(garantir_cobranca_pix(self.mensalidade), antiga)
        antiga.refresh_from_db()
        self.assertEqual((antiga.valor, antiga.conta_recebimento_id, antiga.br_code),
                         (Decimal("120.00"), self.legada.pk, "00020126PIX"))
        client.assert_not_called()

    @patch("integracoes.woovi.services.WooviClient")
    def test_sem_conta_propria_conectada_novas_cobrancas_bloqueadas(self, client):
        with self.assertRaises(RecebimentoNaoConfigurado):
            garantir_cobranca_pix(self.mensalidade)
        conta = preparar_conta(self.academia)
        self.assertFalse(conta.ativa)
        with self.assertRaises(RecebimentoNaoConfigurado):
            garantir_cobranca_pix(self.mensalidade)
        client.assert_not_called()

    @patch("integracoes.woovi.client.requests.request")
    def test_consulta_e_cancelamento_antigos_usam_credencial_antiga(self, request):
        from django.conf import settings
        charge = self.cobranca()
        self.usar_conta_propria()
        request.return_value = self.http({"charge": {"correlationID": charge.correlation_id, "status": "ACTIVE"}})
        conferir_pagamento_pix(self.mensalidade)
        remover_cobranca_pix(self.mensalidade)
        self.assertEqual(request.call_count, 2)
        for call in request.call_args_list:
            self.assertEqual(call.kwargs["headers"]["Authorization"], settings.WOOVI_APP_ID)
        charge.refresh_from_db()
        self.assertEqual(charge.conta_recebimento_id, self.legada.pk)

    def test_credencial_ausente_cruzada_ou_alterada_falha_sem_fallback(self):
        conta = self.usar_conta_propria()
        for valor in ("", "app-id-somente-para-testes"):
            with override_settings(**{conta.credencial_ref: valor}), self.assertRaises(WooviConfigError):
                WooviClient(conta=conta)
        conta.credencial_ref = "WOOVI_APP_ID"
        with self.assertRaises(WooviConfigError):
            WooviClient(conta=conta)
        conta.credencial_ref = referencia_academia(self.academia.pk)
        conta.credencial_fingerprint = fingerprint("segredo-anterior")
        with self.assertRaises(WooviConfigError):
            WooviClient(conta=conta)

    @patch("integracoes.woovi.client.requests.request")
    def test_conta_propria_nao_pode_operar_subconta_nem_repasse(self, request):
        conta = self.usar_conta_propria()
        with self.assertRaises(WooviConfigError):
            WooviClient(conta=conta).obter_subconta("qualquer-chave")
        with self.assertRaises(ValueError):
            solicitar_repasse(conta)
        request.assert_not_called()

    def test_origem_da_conta_e_cobranca_nao_pode_ser_trocada(self):
        antiga = self.cobranca()
        conta = self.usar_conta_propria()
        antiga.conta_recebimento = conta
        with self.assertRaises(ValidationError):
            antiga.save()
        self.legada.credencial_ref = conta.credencial_ref
        with self.assertRaises(ValidationError):
            self.legada.save()

    @override_settings(WOOVI_PLATAFORMA_APP_ID="segredo-plataforma")
    @patch("integracoes.woovi.client.requests.request")
    def test_assinatura_nova_plataforma_e_antiga_origem_original(self, request):
        from django.conf import settings
        self.usar_conta_propria()
        assinatura = Assinatura.objects.create(academia=self.academia, valor_mensal="99.00", inicio=date(2026, 1, 1))
        def resposta(metodo, url, **kw):
            return self.http({"charge": {**kw["json"], "status": "ACTIVE", "brCode": "PIX-SISTEMA"}})
        request.side_effect = resposta
        nova = FaturaAssinatura.objects.create(assinatura=assinatura, competencia=date(2026, 9, 1), valor="99.00", vencimento=date(2026, 9, 10))
        garantir_pix(nova)
        self.assertEqual(request.call_args.kwargs["headers"]["Authorization"], "segredo-plataforma")
        antiga = FaturaAssinatura.objects.create(assinatura=assinatura, competencia=date(2026, 8, 1), valor="99.00", vencimento=date(2026, 8, 10), credencial_ref="WOOVI_APP_ID")
        garantir_pix(antiga)
        self.assertEqual(request.call_args.kwargs["headers"]["Authorization"], settings.WOOVI_APP_ID)

    @patch("integracoes.woovi.assinatura._chaves", return_value=[PEM])
    @patch("integracoes.woovi.views.WooviClient")
    def test_webhook_proprio_assinado_baixa_taxa_real_sem_repasse_e_idempotente(self, origem, chaves):
        self.usar_conta_propria()
        charge = self.cobranca()
        payload = pago(charge.correlation_id)
        payload["charge"]["fee"] = 123
        payload["pix"]["value"] = 12500
        corpo = json.dumps(payload).encode()
        origem.return_value.obter_cobranca.return_value = Cobranca(charge.correlation_id, "COMPLETED", 12500, "", "", None, "tx1", None, 123)
        url = f"/webhooks/woovi/contas/{self.conta.pk}/"
        for _ in range(2):
            resp = self.client.post(url, data=corpo, content_type="application/json", HTTP_X_WEBHOOK_SIGNATURE=assinar(PRIVADA, corpo))
            self.assertEqual(resp.status_code, 200)
        charge.refresh_from_db()
        self.mensalidade.refresh_from_db()
        self.assertEqual((charge.status, self.mensalidade.status), ("paga", "paga"))
        self.assertEqual((charge.valor_pago, charge.taxa, charge.valor_liquido), (Decimal("125"), Decimal("1.23"), Decimal("123.77")))
        self.assertEqual(self.mensalidade.valor_pago, Decimal("125"))
        self.assertEqual(charge.transaction_id, "tx1")
        self.assertEqual(charge.pago_em.day, 8)
        self.assertFalse(Repasse.objects.exists())
        self.assertEqual(EventoWebhook.objects.count(), 1)
        origem.assert_called_once_with(conta=self.conta)

    @patch("integracoes.woovi.assinatura._chaves", return_value=[PEM])
    def test_webhooks_recusam_origem_errada_e_assinatura_ausente(self, chaves):
        antiga = self.cobranca()
        self.usar_conta_propria()
        payload = json.dumps(pago(antiga.correlation_id))
        url = f"/webhooks/woovi/contas/{self.conta.pk}/"
        self.assertEqual(self.client.post(url, data=payload, content_type="application/json").status_code, 400)
        propria = self.cobranca(self.nova_mensalidade(date(2026, 10, 1)), correlation_id="mensalidade-own")
        payload = json.dumps(pago(propria.correlation_id))
        self.assertEqual(self.client.post("/webhooks/woovi/", data=payload, content_type="application/json").status_code, 400)
        self.assertEqual(self.client.post(url, data=payload, content_type="application/json").status_code, 401)
        self.assertFalse(EventoWebhook.objects.exists())

    def test_fee_ausente_nao_inventa_85_centavos_e_pode_ser_completado(self):
        self.usar_conta_propria()
        charge = self.cobranca()
        registrar_pagamento_pix(charge)
        charge.refresh_from_db()
        self.assertIsNone(charge.taxa)
        self.assertIsNone(charge.valor_liquido)
        self.assertFalse(registrar_pagamento_pix(charge, taxa_centavos=0))
        charge.refresh_from_db()
        self.assertEqual(charge.taxa, Decimal("0"))
        self.assertFalse(Repasse.objects.exists())

    @patch("integracoes.woovi.contas.WooviClient")
    @override_settings(SITE_URL="https://sistema.example.com")
    def test_ativacao_preserva_pix_e_repasse_legados(self, client):
        antiga = self.cobranca()
        repasse = Repasse.objects.create(academia=self.academia, conta_recebimento=self.legada, pix_key_destino=self.legada.pix_key)
        conta = preparar_conta(self.academia, self.usuario)
        client.return_value.listar_contas.return_value = [{"accountId": "conta-123", "isDefault": True}]
        client.return_value.obter_conta.return_value = {"accountId": "conta-123", "taxId": self.academia.cnpj}
        conectada = ativar_conta(conta, self.usuario)
        self.assertTrue(conectada.ativa)
        self.assertEqual(conectada.status, ContaRecebimento.CONECTADA)
        self.assertTrue(conectada.taxa_ciente_em)
        self.assertEqual(conectada.credencial_fingerprint, fingerprint(SEGREDO))
        self.legada.refresh_from_db()
        antiga.refresh_from_db()
        repasse.refresh_from_db()
        self.assertFalse(self.legada.ativa)
        self.assertEqual(antiga.status, CobrancaPix.ATIVA)
        self.assertEqual(antiga.conta_recebimento_id, self.legada.pk)
        self.assertEqual(repasse.status, Repasse.PENDENTE)
        client.return_value.configurar_webhook.assert_called_once_with(
            f"https://sistema.example.com/webhooks/woovi/contas/{conta.pk}/", f"keiko-academia-{self.academia.pk}-conta-{conta.pk}")
        ativar_conta(conectada, self.usuario)
        client.return_value.configurar_webhook.assert_called_once()

    @patch("integracoes.woovi.contas.WooviClient")
    def test_cnpj_diferente_nao_ativa_nem_desativa_legado(self, client):
        conta = preparar_conta(self.academia)
        client.return_value.listar_contas.return_value = [{"accountId": "x", "isDefault": True}]
        client.return_value.obter_conta.return_value = {"accountId": "x", "taxId": "00000000000000"}
        with self.assertRaises(ValueError):
            ativar_conta(conta)
        self.assertEqual(ContaRecebimento.ativa_da(self.academia), self.legada)
        client.return_value.configurar_webhook.assert_not_called()

    @patch("integracoes.woovi.contas.WooviClient")
    @override_settings(SITE_URL="https://sistema.example.com")
    def test_onboarding_link_oficial_status_e_nao_conecta_so_por_aprovacao(self, client):
        conta = preparar_conta(self.academia)
        dados = {"correlationID": conta.onboarding_correlation_id, "taxID": {"taxID": self.academia.cnpj}, "status": "APPROVED"}
        client.return_value.iniciar_onboarding.return_value = {"linkOnboarding": "https://kyc.woovi.com/onboarding/teste", "accountRegister": dados}
        iniciar_onboarding(conta)
        self.assertEqual(conta.status, ContaRecebimento.AGUARDANDO)
        self.assertFalse(conta.ativa)
        self.assertIsNone(conta.conectada_em)
        client.return_value.iniciar_onboarding.return_value["linkOnboarding"] = "https://kyc.woovi.com.evil.example/onboarding/x"
        with self.assertRaises(WooviInvalidResponseError):
            iniciar_onboarding(conta)

    @patch("integracoes.woovi.client.requests.request")
    def test_erro_http_e_repr_nao_expoem_credencial(self, request):
        conta = self.usar_conta_propria()
        request.return_value = self.http({"error": "Authorization: " + SEGREDO}, 401)
        client = WooviClient(conta=conta)
        with self.assertRaises(WooviAuthError) as exc:
            client.listar_contas()
        self.assertNotIn(SEGREDO, str(exc.exception))
        self.assertNotIn(SEGREDO, repr(client))

    @patch("integracoes.woovi.client.requests.request")
    def test_onboarding_usa_contexto_dedicado_e_empresa_parceira(self, request):
        request.return_value = self.http({})
        with override_settings(WOOVI_ONBOARDING_APP_ID="segredo-onboarding"):
            WooviClient(contexto="onboarding").iniciar_onboarding(cnpj=self.academia.cnpj, correlation_id="cadastro-1", nome="Keiko", redirect_url="https://example.com")
        kw = request.call_args.kwargs
        self.assertEqual(kw["headers"]["Authorization"], "segredo-onboarding")
        self.assertIs(kw["json"]["partner"], True)

    @patch("integracoes.woovi.client.requests.request")
    def test_webhook_ja_cadastrado_nao_e_duplicado(self, request):
        conta = self.usar_conta_propria()
        url = "https://example.com/webhook"
        request.return_value = self.http({"webhooks": [{"url": url, "event": "OPENPIX:CHARGE_COMPLETED", "isActive": True}]})
        WooviClient(conta=conta).configurar_webhook(url, "keiko")
        request.assert_called_once()
        self.assertEqual(request.call_args.args[0], "GET")

    @patch("integracoes.woovi.views.assinatura_valida", return_value=True)
    @patch("integracoes.woovi.views.WooviClient")
    def test_evento_assinado_de_outra_transacao_nao_baixa_assinatura(self, origem, assinatura):
        assinatura_model = Assinatura.objects.create(academia=self.academia, valor_mensal="99.00", inicio=date(2026, 1, 1))
        fatura = FaturaAssinatura.objects.create(assinatura=assinatura_model, competencia=date(2026, 9, 1), valor="99.00", vencimento=date(2026, 9, 10))
        correlation = f"assinatura-{fatura.pk}-1"
        origem.return_value.obter_cobranca.return_value = Cobranca(correlation, "COMPLETED", 9900, "", "", None, "tx-plataforma", None)
        resposta = self.client.post("/webhooks/woovi/", data=json.dumps(pago(correlation)), content_type="application/json")
        self.assertEqual(resposta.status_code, 400)
        fatura.refresh_from_db()
        self.assertEqual(fatura.status, FaturaAssinatura.PENDENTE)
        self.assertFalse(EventoWebhook.objects.exists())
        self.assertEqual(origem.call_args.kwargs["contexto"], "plataforma")

    @patch("integracoes.woovi.views.assinatura_valida", return_value=True)
    @patch("integracoes.woovi.views.WooviClient")
    def test_origem_indisponivel_nao_baixa_e_retentativa_pode_confirmar(self, origem, assinatura):
        self.usar_conta_propria()
        charge = self.cobranca()
        url = f"/webhooks/woovi/contas/{self.conta.pk}/"
        corpo = json.dumps(pago(charge.correlation_id))
        origem.return_value.obter_cobranca.side_effect = WooviUnavailableError("indisponivel")
        self.assertEqual(self.client.post(url, data=corpo, content_type="application/json").status_code, 503)
        self.assertFalse(EventoWebhook.objects.exists())
        self.mensalidade.refresh_from_db()
        self.assertEqual(self.mensalidade.status, "pendente")
        origem.return_value.obter_cobranca.side_effect = None
        origem.return_value.obter_cobranca.return_value = Cobranca(charge.correlation_id, "COMPLETED", 12000, "", "", None, "tx1", None, 85)
        self.assertEqual(self.client.post(url, data=corpo, content_type="application/json").status_code, 200)
        self.mensalidade.refresh_from_db()
        self.assertEqual(self.mensalidade.status, "paga")

    @patch("integracoes.woovi.repasses.WooviClient")
    def test_pagamento_legado_apos_ativacao_ainda_processa_repasse_original(self, client):
        from integracoes.woovi.repasses import processar_repasse
        from tests.woovi_base import subconta, saque
        charge = self.cobranca()
        self.usar_conta_propria()
        registrar_pagamento_pix(charge, taxa_centavos=85)
        repasse = Repasse.objects.get()
        client.return_value.obter_subconta.return_value = subconta(saldo=11915)
        client.return_value.sacar_subconta.return_value = saque(11815)
        processar_repasse(repasse.pk)
        client.assert_called_once_with(conta=self.legada)
        client.return_value.creditar_subconta.assert_called_once()
        client.return_value.sacar_subconta.assert_called_once()

    def test_fatura_com_pix_nao_permite_troca_da_origem(self):
        assinatura = Assinatura.objects.create(academia=self.academia, valor_mensal="99.00", inicio=date(2026, 1, 1))
        fatura = FaturaAssinatura.objects.create(assinatura=assinatura, competencia=date(2026, 9, 1), valor="99.00", vencimento=date(2026, 9, 10), correlation_id="assinatura-1-1")
        fatura.credencial_ref = referencia_academia(self.academia.pk)
        with self.assertRaises(ValidationError):
            fatura.save()

    @patch("integracoes.woovi.client.requests.request")
    def test_criar_webhook_exige_confirmacao_do_provedor(self, request):
        conta = self.usar_conta_propria()
        request.side_effect = [self.http({"webhooks": []}), self.http({})]
        with self.assertRaises(WooviInvalidResponseError):
            WooviClient(conta=conta).configurar_webhook("https://example.com/hook", "keiko")

    @patch("integracoes.woovi.views.assinatura_valida", return_value=True)
    @patch("integracoes.woovi.client.requests.request")
    def test_webhook_legado_confirma_na_conta_antiga_mesmo_com_conta_propria(self, request, assinatura):
        from django.conf import settings
        charge = self.cobranca()
        self.usar_conta_propria()
        payload = pago(charge.correlation_id)
        request.return_value = self.http({"charge": {**payload["charge"], "transactionID": "tx-outra-conta"}})
        resp = self.client.post("/webhooks/woovi/", data=json.dumps(payload), content_type="application/json")
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(Repasse.objects.exists())
        self.assertFalse(EventoWebhook.objects.exists())
        self.assertEqual(request.call_args.kwargs["headers"]["Authorization"], settings.WOOVI_APP_ID)
        request.return_value = self.http({"charge": payload["charge"]})
        resp = self.client.post("/webhooks/woovi/", data=json.dumps(payload), content_type="application/json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Repasse.objects.get().conta_recebimento_id, self.legada.pk)
