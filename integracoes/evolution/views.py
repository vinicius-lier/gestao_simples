"""Webhook opcional de eventos de conexão da Evolution.

NÃO é obrigatório para a FASE 2A — o painel funciona com o botão
"Verificar status". Quando a Evolution estiver configurada para enviar
eventos, este endpoint reflete ``connection.update`` / ``qrcode.updated`` no
``IntegracaoWhatsApp``.

Regras de segurança:
  * exige o cabeçalho ``x-evolution-token`` == ``settings.EVOLUTION_WEBHOOK_TOKEN``
    (obrigatório quando ``EVOLUTION_WEBHOOK_REQUIRE_TOKEN``);
  * localiza a academia só pela instância (nome), validada contra o banco;
  * atualiza APENAS o status da integração — nunca dispara cobrança;
  * qualquer identificador desconhecido é ignorado (200 + ignorado).
"""
import json

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from academias.models import IntegracaoWhatsApp
from integracoes.evolution.services import (
    extrair_estado,
    extrair_numero,
    mapear_status,
    persistir_status,
)

_EVENTOS_QRCODE = {"qrcode.updated", "QRCODE_UPDATED"}


@csrf_exempt
@require_http_methods(["POST"])
def webhook_conexao(request, instancia=None):
    exige_token = getattr(
        settings, "EVOLUTION_WEBHOOK_REQUIRE_TOKEN", not settings.DEBUG
    )
    token_esperado = getattr(settings, "EVOLUTION_WEBHOOK_TOKEN", "")

    if exige_token and not token_esperado:
        return JsonResponse({"detail": "webhook da Evolution não configurado"}, status=503)

    if token_esperado and request.headers.get("x-evolution-token") != token_esperado:
        return JsonResponse({"detail": "token inválido"}, status=401)

    try:
        payload = json.loads(request.body or b"{}")
    except (TypeError, ValueError):
        return JsonResponse({"detail": "corpo inválido"}, status=400)

    if not isinstance(payload, dict):
        return JsonResponse({"detail": "corpo inválido"}, status=400)

    nome_instancia = instancia or payload.get("instance") or payload.get("instanceName")
    if not nome_instancia:
        return JsonResponse({"ignorado": True})

    config = IntegracaoWhatsApp.objects.filter(
        evolution_instance_name=nome_instancia,
        provider=IntegracaoWhatsApp.PROVIDER_EVOLUTION,
    ).first()
    if config is None:
        return JsonResponse({"ignorado": True})

    evento = payload.get("event") or ""
    dados = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    estado = extrair_estado(dados) or extrair_estado(payload)

    if estado:
        persistir_status(
            config, mapear_status(estado), bruto=estado, numero=extrair_numero(dados)
        )
        return JsonResponse({"ok": True})

    if evento in _EVENTOS_QRCODE:
        persistir_status(
            config, IntegracaoWhatsApp.STATUS_AGUARDANDO_QRCODE, bruto="qrcode.updated"
        )
        return JsonResponse({"ok": True})

    return JsonResponse({"ignorado": True})
