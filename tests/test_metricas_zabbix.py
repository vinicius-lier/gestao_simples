from datetime import timedelta
from unittest.mock import patch

from django.db import DatabaseError
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from academias.models import Academia, IntegracaoWhatsApp
from financeiro.models import ContaRecebimento, EventoWebhook, LembreteCobranca, Repasse
from monitoramento.models import AlertaEnviado
from tests.woovi_base import CenarioWoovi, CHAVE

TOKEN = "somente-testes-metricas-nao-e-credencial-real"
ROTA = "/monitoramento/metricas/"


@override_settings(MONITORAMENTO_TOKEN=TOKEN)
class AcessoMetricasTests(SimpleTestCase):
    def test_negado_sem_token_errado_ou_token_na_query(self):
        with patch("monitoramento.views.coletar") as coletar:
            for cabecalho in ("", "Bearer errado"):
                self.assertEqual(self.client.get(ROTA + "?token=" + TOKEN, HTTP_AUTHORIZATION=cabecalho).status_code, 404)
            coletar.assert_not_called()

    @override_settings(MONITORAMENTO_TOKEN="")
    def test_rota_desabilitada_por_padrao(self):
        self.assertEqual(self.client.get(ROTA).status_code, 404)

    @override_settings(MONITORAMENTO_TOKEN="curto")
    def test_chave_curta_nao_ativa_rota(self):
        self.assertEqual(self.client.get(ROTA, HTTP_AUTHORIZATION="Bearer curto").status_code, 404)

    def test_post_nao_coleta(self):
        with patch("monitoramento.views.coletar") as coletar:
            self.assertEqual(self.client.post(ROTA, HTTP_AUTHORIZATION="Bearer " + TOKEN).status_code, 405)
            coletar.assert_not_called()

    def test_falha_nao_vira_sucesso_ou_expoe_excecao(self):
        with patch("monitoramento.views.coletar", side_effect=DatabaseError("dados privados")):
            resposta = self.client.get(ROTA, HTTP_AUTHORIZATION="Bearer " + TOKEN)
        self.assertEqual(resposta.status_code, 503)
        self.assertEqual(resposta.json(), {"status": "erro"})

    def test_resposta_nao_pode_ser_cacheada(self):
        with patch("monitoramento.views.coletar", return_value={"schema": 1}):
            resposta = self.client.get(ROTA, HTTP_AUTHORIZATION="Bearer " + TOKEN)
        self.assertEqual(resposta.status_code, 200)
        self.assertIn("no-store", resposta["Cache-Control"])


@override_settings(MONITORAMENTO_TOKEN=TOKEN, ALERTAS_DISCORD_WEBHOOK_URL="")
class ContagensTests(CenarioWoovi, TestCase):
    def setUp(self):
        self.criar_cenario()

    def dados(self):
        resposta = self.client.get(ROTA, HTTP_AUTHORIZATION="Bearer " + TOKEN)
        self.assertEqual(resposta.status_code, 200)
        self.assertNotIn(CHAVE, resposta.content.decode())
        self.assertNotIn(self.academia.nome, resposta.content.decode())
        return resposta.json()

    def test_sem_dados_nao_inventa_falhas(self):
        dados = self.dados()
        self.assertEqual(sum(dados.values()), 1)  # Somente schema; captura desativada é explícita.

    def test_repasse_incerto_e_falha_persistem_sem_novo_saque(self):
        repasse = Repasse.objects.create(academia=self.academia, conta_recebimento=self.conta,
                                        pix_key_destino=CHAVE, reconciliar=True)
        self.assertEqual(self.dados()["repasses_com_falha"], 1)
        repasse.refresh_from_db()
        self.assertTrue(repasse.reconciliar)
        self.assertEqual(repasse.tentativas, 0)
        Repasse.objects.filter(pk=repasse.pk).update(status=Repasse.REQUER_ATENCAO, reconciliar=False)
        self.assertEqual(self.dados()["repasses_com_falha"], 1)
        Repasse.objects.filter(pk=repasse.pk).update(status=Repasse.CONCLUIDA)
        self.assertEqual(self.dados()["repasses_com_falha"], 0)

    def test_repasse_envelhecido_e_recente(self):
        repasse = Repasse.objects.create(academia=self.academia, conta_recebimento=self.conta, pix_key_destino=CHAVE)
        self.assertEqual(self.dados()["repasses_pendentes_30m"], 0)
        Repasse.objects.filter(pk=repasse.pk).update(atualizado_em=timezone.now() - timedelta(minutes=31))
        self.assertEqual(self.dados()["repasses_pendentes_30m"], 1)

    def test_webhooks_processados_nao_sao_pendencias(self):
        antigo = EventoWebhook.objects.create(chave="pendente", tipo="OPENPIX:CHARGE_COMPLETED")
        EventoWebhook.objects.create(chave="recente", tipo="OPENPIX:CHARGE_COMPLETED")
        EventoWebhook.objects.create(chave="erro", tipo="OPENPIX:CHARGE_COMPLETED", status=EventoWebhook.ERRO)
        EventoWebhook.objects.create(chave="processado", tipo="OPENPIX:CHARGE_COMPLETED", status=EventoWebhook.PROCESSADO)
        EventoWebhook.objects.filter(pk=antigo.pk).update(recebido_em=timezone.now() - timedelta(minutes=6))
        dados = self.dados()
        self.assertEqual(dados["webhooks_com_erro"], 1)
        self.assertEqual(dados["webhooks_pendentes_5m"], 1)

    def test_erro_de_lembrete_nao_exige_envio_de_mensalidade_paga(self):
        LembreteCobranca.objects.create(mensalidade=self.mensalidade, estagio=LembreteCobranca.VENCIMENTO,
                                        status=LembreteCobranca.ERRO)
        self.assertEqual(self.dados()["lembretes_com_erro"], 1)
        self.mensalidade.status = "paga"
        self.mensalidade.save(update_fields=["status"])
        self.assertEqual(self.dados()["lembretes_com_erro"], 0)

    def test_whatsapp_novo_e_conectado_nao_geram_falso_positivo(self):
        config = IntegracaoWhatsApp.objects.create(academia=self.academia,
            provider=IntegracaoWhatsApp.PROVIDER_EVOLUTION, evolution_instance_name="teste")
        self.assertEqual(self.dados()["whatsapp_desconectados"], 0)
        config.ultima_conexao_em = timezone.now()
        config.save(update_fields=["ultima_conexao_em"])
        self.assertEqual(self.dados()["whatsapp_desconectados"], 1)
        config.status_conexao = IntegracaoWhatsApp.STATUS_CONECTADO
        config.save(update_fields=["status_conexao"])
        self.assertEqual(self.dados()["whatsapp_desconectados"], 0)

    def test_agrega_negocios_sem_expor_identidades(self):
        outra = Academia.objects.create(nome="Outro negocio privado", cnpj="monitoramento2")
        for academia in (self.academia, outra):
            ContaRecebimento.objects.create(academia=academia, status=ContaRecebimento.ERRO, ativa=False)
        self.assertEqual(self.dados()["contas_com_erro"], 2)

    def test_erros_sao_tipos_registrados_e_captura_desativada_e_visivel(self):
        AlertaEnviado.objects.create(chave="erro:ValueError:privado", ultimo_envio=timezone.now())
        AlertaEnviado.objects.create(chave="alerta:financeiro", ultimo_envio=timezone.now())
        AlertaEnviado.objects.create(chave="erro:antigo", ultimo_envio=timezone.now() - timedelta(days=2))
        dados = self.dados()
        self.assertEqual(dados["tipos_erro_registrados_24h"], 1)
        self.assertEqual(dados["captura_erros_configurada"], 0)
