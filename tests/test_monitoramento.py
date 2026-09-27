"""Monitoramento: alertas no Discord (erros 500, alertas da plataforma,
RAM/disco da VPS) sem repetir o mesmo aviso a cada ocorrência."""
import tempfile
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

import requests
from django.core.management import call_command
from django.db import DatabaseError
from django.test import Client, TestCase, override_settings
from django.urls import path
from django.utils import timezone

from financeiro.alertas import alertar_plataforma
from monitoramento import alertas
from monitoramento.alertas import enviar_alerta
from monitoramento.models import AlertaEnviado
from monitoramento.servidor import uso_ram

WEBHOOK = "https://discord.example.com/api/webhooks/1/segredo"


def _quebra(request, token):
    raise ValueError("falha de teste")


urlpatterns = [path("quebra/<str:token>/", _quebra)]


def texto_do_alerta(post):
    """Título, descrição e seções do último alerta enviado, num texto só."""
    embed = post.call_args.kwargs["json"]["embeds"][0]
    partes = [embed["title"], embed["description"]] + [f"{c['name']}: {c['value']}" for c in embed["fields"]]
    return " | ".join(partes)


@override_settings(ALERTAS_DISCORD_WEBHOOK_URL=WEBHOOK, ALERTAS_EM_SEGUNDO_PLANO=False)
@patch("monitoramento.alertas.requests.post")
class EnviarAlertaTests(TestCase):
    def test_sem_webhook_nao_envia(self, post):
        with override_settings(ALERTAS_DISCORD_WEBHOOK_URL=""):
            self.assertFalse(enviar_alerta("t", "x", "k"))
        post.assert_not_called()

    def test_envia_embed_para_o_webhook(self, post):
        self.assertTrue(enviar_alerta("Título", "Texto", "k"))
        url, = post.call_args.args
        embed = post.call_args.kwargs["json"]["embeds"][0]
        self.assertEqual((url, embed["title"], embed["description"]), (WEBHOOK, "Título", "Texto"))

    def test_mesmo_alerta_so_uma_vez_por_intervalo_e_conta_repeticoes(self, post):
        enviar_alerta("t", "x", "k")
        self.assertFalse(enviar_alerta("t", "x", "k"))
        self.assertFalse(enviar_alerta("t", "x", "k"))
        self.assertEqual(post.call_count, 1)

        AlertaEnviado.objects.filter(chave="k").update(ultimo_envio=timezone.now() - timedelta(hours=2))
        self.assertTrue(enviar_alerta("t", "x", "k"))
        self.assertIn("mais 2 vez(es)", post.call_args.kwargs["json"]["embeds"][0]["description"])

    def test_discord_fora_do_ar_nao_derruba_e_nao_loga_a_url(self, post):
        post.side_effect = requests.ConnectionError(WEBHOOK)
        with self.assertLogs("monitoramento", level="WARNING") as log:
            self.assertFalse(enviar_alerta("t", "x", "k"))
        self.assertNotIn("segredo", "".join(log.output))

    def test_banco_fora_do_ar_ainda_avisa_uma_vez(self, post):
        alertas._ultimos_sem_banco.clear()
        with patch.object(AlertaEnviado.objects, "select_for_update", side_effect=DatabaseError):
            self.assertTrue(enviar_alerta("t", "x", "sem-banco"))
            self.assertFalse(enviar_alerta("t", "x", "sem-banco"))
        self.assertEqual(post.call_count, 1)


@override_settings(ALERTAS_DISCORD_WEBHOOK_URL=WEBHOOK, ALERTAS_EM_SEGUNDO_PLANO=False, ROOT_URLCONF=__name__)
@patch("monitoramento.alertas.requests.post")
class HandlerDeLogTests(TestCase):
    def test_erro_500_vira_alerta_sem_o_token_da_url(self, post):
        token = "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"
        resposta = Client(raise_request_exception=False).get(f"/quebra/{token}/")

        self.assertEqual(resposta.status_code, 500)
        texto = texto_do_alerta(post)
        self.assertIn("ValueError", texto)
        self.assertIn("GET /quebra/[token]/", texto)
        self.assertIn("tests/test_monitoramento.py", texto)
        self.assertIn("O que fazer", texto)
        self.assertNotIn(token, texto)

    def test_alerta_da_plataforma_vai_para_o_discord(self, post):
        alertar_plataforma("Mensalidade paga em dobro (Pix pago depois de outra baixa)", mensalidade=7)

        texto = texto_do_alerta(post)
        self.assertIn("Mensalidade paga em dobro", texto)
        self.assertIn("O que isso significa", texto)
        self.assertIn("devolver o valor", texto)  # explicação específica deste alerta
        self.assertIn("mensalidade=7", texto)
        self.assertTrue(AlertaEnviado.objects.filter(chave__startswith="alerta:Mensalidade paga em dobro").exists())

    def test_alerta_desconhecido_tem_explicacao_padrao(self, post):
        alertar_plataforma("Situação nova qualquer")
        self.assertIn("O que fazer", texto_do_alerta(post))


class UsoRamTests(TestCase):
    def test_calcula_a_partir_do_meminfo(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as arquivo:
            arquivo.write("MemTotal:        8000000 kB\nMemFree:  100 kB\nMemAvailable:    2000000 kB\n")
        self.assertAlmostEqual(uso_ram(arquivo.name), 75.0)

    def test_sem_meminfo_devolve_none(self):
        self.assertIsNone(uso_ram("/caminho/que/nao/existe"))


@patch("monitoramento.management.commands.verificar_servidor.enviar_alerta")
class VerificarServidorTests(TestCase):
    def rodar(self, ram, disco):
        saida = StringIO()
        with patch("monitoramento.management.commands.verificar_servidor.uso_ram", return_value=ram), \
                patch("monitoramento.management.commands.verificar_servidor.uso_disco", return_value=disco):
            call_command("verificar_servidor", stdout=saida)
        return saida.getvalue()

    def test_dentro_do_limite_nao_avisa(self, alerta):
        self.assertIn("RAM 40% | Disco 50%", self.rodar(40, 50))
        alerta.assert_not_called()

    @override_settings(MONITORAMENTO_LIMITE_RAM=90, MONITORAMENTO_LIMITE_DISCO=85)
    def test_acima_do_limite_avisa_cada_recurso(self, alerta):
        self.rodar(95, 90)
        chaves = [chamada.args[2] for chamada in alerta.call_args_list]
        self.assertEqual(chaves, ["servidor:ram", "servidor:disco"])
        self.assertEqual(alerta.call_args.kwargs["intervalo"], timedelta(hours=6))
        secoes = dict(alerta.call_args.kwargs["campos"])
        self.assertIn("Docker Cleanup", secoes["O que fazer"])
