"""FASE 1E — contrato Django -> n8n (payload + client)."""
from datetime import date
from decimal import Decimal
from unittest.mock import Mock, patch

import requests
from django.test import SimpleTestCase, TestCase, override_settings

from academias.models import Academia, IntegracaoWhatsApp
from atletas.models import Atleta, Responsavel
from financeiro.models import LembreteCobranca, Mensalidade
from integracoes.n8n.client import N8nAPIError, N8nClient
from integracoes.n8n.services import CAMPOS_PROIBIDOS, montar_payload_cobranca
from matriculas.models import Matricula
from modalidades.models import Modalidade


class MontarPayloadCobrancaTests(TestCase):
    def setUp(self):
        self.academia = Academia.objects.create(nome="Escola Judô", cnpj="N8N1")
        self.responsavel = Responsavel.objects.create(
            academia=self.academia, nome="Ana Paula", cpf="12345678901",
            whatsapp="(21) 99999-9999", email="ana@example.com",
        )
        self.atleta = Atleta.objects.create(
            academia=self.academia, nome="Bruno", responsavel_financeiro=self.responsavel
        )
        self.modalidade = Modalidade.objects.create(academia=self.academia, nome="Judô")
        self.matricula = Matricula.objects.create(
            academia=self.academia, atleta=self.atleta, modalidade=self.modalidade,
            valor_mensalidade=Decimal("150.00"), dia_vencimento=10, data_inicio=date(2026, 1, 1),
        )
        self.mensalidade = Mensalidade.objects.create(
            academia=self.academia, matricula=self.matricula, competencia=date(2026, 9, 1),
            valor=Decimal("150.00"), vencimento=date(2026, 9, 10), status="pendente",
        )

    def test_campos_principais(self):
        payload = montar_payload_cobranca(
            self.mensalidade, LembreteCobranca.CINCO_DIAS,
            link_pagamento="https://site/responsavel/entrar/tok?next=/x",
            pix_copia_e_cola="00020126BR.GOV.BCB.PIX",
            hoje=date(2026, 9, 5),
        )
        self.assertEqual(payload["evento"], "cobranca_lembrete")
        self.assertEqual(
            payload["idempotency_key"], f"{self.mensalidade.pk}:5_dias"
        )
        self.assertEqual(payload["academia_id"], self.academia.pk)
        self.assertEqual(payload["academia_nome"], "Escola Judô")
        self.assertEqual(payload["mensalidade_id"], self.mensalidade.pk)
        self.assertEqual(payload["tipo_lembrete"], "5_dias")
        self.assertEqual(payload["status_mensalidade"], "pendente")
        self.assertEqual(payload["competencia"], "2026-09")
        self.assertEqual(payload["valor"], "150.00")
        self.assertEqual(payload["vencimento"], "2026-09-10")
        self.assertEqual(payload["dias_para_vencer"], 5)
        self.assertEqual(payload["aluno_nome"], "Bruno")
        self.assertEqual(payload["modalidade"], "Judô")
        self.assertEqual(payload["responsavel_id"], self.responsavel.pk)
        self.assertEqual(payload["responsavel_nome"], "Ana Paula")
        self.assertEqual(payload["telefone"], "5521999999999")
        self.assertEqual(payload["pix_copia_e_cola"], "00020126BR.GOV.BCB.PIX")
        # A família só recebe links do próprio sistema.
        self.assertNotIn("link_pagamento_pix", payload)

    def test_sem_dados_pessoais_ou_segredos(self):
        payload = montar_payload_cobranca(
            self.mensalidade, LembreteCobranca.ATRASADA,
            link_pagamento="https://x", pix_copia_e_cola="pix",
        )
        for proibido in CAMPOS_PROIBIDOS:
            self.assertNotIn(proibido, payload)
        # valores sensíveis não podem vazar como conteúdo
        blob = repr(payload)
        self.assertNotIn("12345678901", blob)         # CPF
        self.assertNotIn("ana@example.com", blob)      # e-mail

    def test_instancia_whatsapp_vem_da_configuracao(self):
        IntegracaoWhatsApp.objects.create(
            academia=self.academia, provider=IntegracaoWhatsApp.PROVIDER_EVOLUTION,
            evolution_instance_name="escola-judo-01",
        )
        payload = montar_payload_cobranca(
            self.mensalidade, LembreteCobranca.VENCIMENTO, link_pagamento="https://x"
        )
        self.assertEqual(payload["instancia_whatsapp"], "escola-judo-01")

    def test_instancia_whatsapp_vazia_sem_configuracao(self):
        payload = montar_payload_cobranca(
            self.mensalidade, LembreteCobranca.VENCIMENTO, link_pagamento="https://x"
        )
        self.assertEqual(payload["instancia_whatsapp"], "")

    def test_qrcode_base64_nunca_entra(self):
        payload = montar_payload_cobranca(
            self.mensalidade, LembreteCobranca.UM_DIA, link_pagamento="https://x"
        )
        self.assertNotIn("pix_qrcode_base64", payload)

    def test_sem_responsavel_levanta_erro(self):
        self.atleta.responsavel_financeiro = None
        self.atleta.save(update_fields=["responsavel_financeiro"])
        with self.assertRaises(ValueError):
            montar_payload_cobranca(self.mensalidade, LembreteCobranca.ATRASADA)


class PrepararPayloadN8nTests(TestCase):
    """Sequência FASE 2 preparada em financeiro.lembretes (não acionada
    ainda por enviar_lembretes)."""

    def setUp(self):
        self.academia = Academia.objects.create(nome="Ac", cnpj="PPN1")
        self.responsavel = Responsavel.objects.create(
            academia=self.academia, nome="Ana", cpf="12345678901", whatsapp="21999999999"
        )
        self.atleta = Atleta.objects.create(
            academia=self.academia, nome="Bruno", responsavel_financeiro=self.responsavel
        )
        self.modalidade = Modalidade.objects.create(academia=self.academia, nome="Judô")
        self.matricula = Matricula.objects.create(
            academia=self.academia, atleta=self.atleta, modalidade=self.modalidade,
            valor_mensalidade=Decimal("150.00"), dia_vencimento=10, data_inicio=date(2026, 1, 1),
        )
        self.mensalidade = Mensalidade.objects.create(
            academia=self.academia, matricula=self.matricula, competencia=date(2026, 9, 1),
            valor=Decimal("150.00"), vencimento=date(2026, 9, 10), status="pendente",
        )

    @patch("integracoes.woovi.services.garantir_cobranca_pix")
    def test_monta_com_pix_e_link(self, mock_garantir):
        from financeiro.lembretes import preparar_payload_n8n

        from types import SimpleNamespace

        mock_garantir.return_value = SimpleNamespace(br_code="00020126PIX")

        payload = preparar_payload_n8n(self.mensalidade, LembreteCobranca.CINCO_DIAS)

        mock_garantir.assert_called_once()
        self.assertEqual(payload["pix_copia_e_cola"], "00020126PIX")
        self.assertIn("/responsavel/entrar/", payload["link_pagamento"])
        self.assertEqual(
            payload["idempotency_key"], f"{self.mensalidade.pk}:5_dias"
        )

    @patch("integracoes.woovi.services.garantir_cobranca_pix")
    def test_sem_pix_ainda_monta_com_link(self, mock_garantir):
        from financeiro.lembretes import preparar_payload_n8n
        from integracoes.woovi.exceptions import WooviError

        mock_garantir.side_effect = WooviError("timeout")
        payload = preparar_payload_n8n(self.mensalidade, LembreteCobranca.ATRASADA)

        self.assertEqual(payload["pix_copia_e_cola"], "")
        self.assertIn("/responsavel/entrar/", payload["link_pagamento"])


class N8nClientTests(SimpleTestCase):
    @override_settings(N8N_WEBHOOK_URL="", N8N_TOKEN="")
    def test_sem_url_levanta_value_error(self):
        with self.assertRaisesMessage(ValueError, "N8N_WEBHOOK_URL"):
            N8nClient()

    @override_settings(
        N8N_WEBHOOK_URL="https://n8n.example.com/webhook/segredo-xyz",
        N8N_TOKEN="tok-super-secreto",
        N8N_TIMEOUT=7,
    )
    @patch("integracoes.n8n.client.requests.post")
    def test_post_ok_envia_header_e_timeout(self, mock_post):
        resp = Mock(status_code=200)
        resp.json.return_value = {"ok": True}
        mock_post.return_value = resp

        resultado = N8nClient().enviar({"evento": "x"})

        self.assertEqual(resultado, {"ok": True})
        _, kwargs = mock_post.call_args
        self.assertEqual(kwargs["timeout"], 7)
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer tok-super-secreto")

    @override_settings(N8N_WEBHOOK_URL="https://n8n.example.com/webhook/s", N8N_TOKEN="")
    @patch("integracoes.n8n.client.requests.post")
    def test_status_nao_2xx_vira_n8n_api_error(self, mock_post):
        mock_post.return_value = Mock(status_code=500)
        with self.assertRaisesMessage(N8nAPIError, "HTTP 500"):
            N8nClient().enviar({})

    @override_settings(
        N8N_WEBHOOK_URL="https://n8n.example.com/webhook/segredo-xyz",
        N8N_TOKEN="tok-super-secreto",
    )
    @patch("integracoes.n8n.client.requests.post")
    def test_erro_de_conexao_sanitizado(self, mock_post):
        mock_post.side_effect = requests.ConnectionError(
            "falha ao conectar em https://n8n.example.com/webhook/segredo-xyz"
        )
        with self.assertRaises(N8nAPIError) as ctx:
            N8nClient().enviar({})
        msg = str(ctx.exception)
        self.assertNotIn("segredo-xyz", msg)
        self.assertNotIn("tok-super-secreto", msg)

    @override_settings(
        N8N_WEBHOOK_URL="https://n8n.example.com/webhook/segredo-xyz",
        N8N_TOKEN="tok-super-secreto",
    )
    def test_sanitizar_remove_url_e_token(self):
        client = N8nClient()
        sujo = "erro em https://n8n.example.com/webhook/segredo-xyz com tok-super-secreto"
        limpo = client._sanitizar(sujo)
        self.assertNotIn("segredo-xyz", limpo)
        self.assertNotIn("tok-super-secreto", limpo)
