"""Alertas para quem opera a plataforma, num canal do Discord (webhook em
``ALERTAS_DISCORD_WEBHOOK_URL``). Nunca vão para o WhatsApp de uma academia.

O mesmo alerta (``chave``) é avisado no máximo uma vez por ``intervalo``; as
ocorrências nesse meio-tempo são contadas e aparecem no aviso seguinte.
Melhor esforço: uma falha ao avisar nunca derruba quem chamou.
"""
import logging
import threading
from datetime import timedelta

import requests
from django.conf import settings
from django.db import DatabaseError, transaction
from django.utils import timezone

logger = logging.getLogger("monitoramento")

CORES = {"erro": 0xB5242C, "aviso": 0xE8A33D, "ok": 0x2E8B57}

# Só quando o banco está fora do ar: aí a repetição é controlada por processo.
_ultimos_sem_banco = {}


def enviar_alerta(titulo, texto, chave, *, campos=None, nivel="erro", intervalo=timedelta(hours=1),
                  em_segundo_plano=False):
    """Posta o alerta no Discord. ``campos`` são seções com título, como
    [("O que isso significa", "..."), ("O que fazer", "...")]. Devolve True
    se enviou (ou disparou o envio, em segundo plano) e False se não há
    webhook, se o mesmo alerta já foi avisado dentro do intervalo ou se o
    Discord recusou."""
    url = getattr(settings, "ALERTAS_DISCORD_WEBHOOK_URL", "")
    if not url:
        return False
    pode, repeticoes = _pode_enviar(chave[:200], intervalo)
    if not pode:
        return False
    if repeticoes:
        texto += f"\n\n_Aconteceu mais {repeticoes} vez(es) desde o aviso anterior._"
    corpo = {
        "username": "Gestão Simples",
        "embeds": [{
            "title": titulo[:256],
            "description": texto[:4000],
            "color": CORES.get(nivel, CORES["erro"]),
            "fields": [{"name": nome[:256], "value": valor[:1024], "inline": False} for nome, valor in campos or []],
            "timestamp": timezone.now().isoformat(),
        }],
    }
    if em_segundo_plano:
        # Numa request com erro, o aviso não pode segurar a resposta.
        threading.Thread(target=_postar, args=(url, corpo), daemon=True).start()
        return True
    return _postar(url, corpo)


def _postar(url, corpo):
    try:
        resposta = requests.post(url, json=corpo, timeout=10)
        resposta.raise_for_status()
    except requests.RequestException as exc:
        # Sem a URL no log: quem a tem consegue postar no canal.
        logger.warning("Discord: alerta não enviado (%s).", type(exc).__name__)
        return False
    return True


def _pode_enviar(chave, intervalo):
    """(pode avisar agora?, ocorrências desde o último aviso)."""
    from .models import AlertaEnviado

    agora = timezone.now()
    try:
        with transaction.atomic():
            alerta, criado = AlertaEnviado.objects.select_for_update().get_or_create(
                chave=chave, defaults={"ultimo_envio": agora},
            )
            if criado:
                return True, 0
            if alerta.ultimo_envio > agora - intervalo:
                alerta.repeticoes += 1
                alerta.save(update_fields=["repeticoes"])
                return False, 0
            repeticoes = alerta.repeticoes
            alerta.ultimo_envio, alerta.repeticoes = agora, 0
            alerta.save(update_fields=["ultimo_envio", "repeticoes"])
            return True, repeticoes
    except DatabaseError:
        # Banco fora do ar é justamente quando o aviso mais importa.
        ultimo = _ultimos_sem_banco.get(chave)
        if ultimo and ultimo > agora - intervalo:
            return False, 0
        _ultimos_sem_banco[chave] = agora
        return True, 0
