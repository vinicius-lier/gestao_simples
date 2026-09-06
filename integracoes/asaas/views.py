import json

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from financeiro.models import Mensalidade
from financeiro.services import registrar_pagamento

# Eventos do Asaas que indicam que o dinheiro entrou. Ver:
# https://docs.asaas.com/docs/webhook-eventos
EVENTOS_PAGO = {"PAYMENT_RECEIVED", "PAYMENT_CONFIRMED"}

MAPA_FORMA_PAGAMENTO = {
    "PIX": "pix",
    "BOLETO": "boleto",
    "CREDIT_CARD": "cartao",
    "UNDEFINED": "outro",
}


@csrf_exempt
@require_POST
def webhook_pagamento(request):
    """Recebe a notificação do Asaas quando um pagamento é confirmado e
    marca a mensalidade correspondente como paga — sem precisar de
    conferência manual.

    Segurança: configure ASAAS_WEBHOOK_TOKEN (o mesmo token cadastrado no
    painel do Asaas ao criar o webhook) para exigir o cabeçalho
    'asaas-access-token' em toda chamada. Sem o token configurado, o
    endpoint aceita qualquer chamada — use isso só em desenvolvimento.
    """
    token_esperado = getattr(settings, "ASAAS_WEBHOOK_TOKEN", "")
    if token_esperado and request.headers.get("asaas-access-token") != token_esperado:
        return JsonResponse({"detail": "token inválido"}, status=401)

    try:
        payload = json.loads(request.body or b"{}")
    except (TypeError, ValueError):
        return JsonResponse({"detail": "corpo inválido"}, status=400)

    if not isinstance(payload, dict):
        return JsonResponse({"detail": "corpo inválido"}, status=400)

    evento = payload.get("event")
    pagamento = payload.get("payment")
    pagamento = pagamento if isinstance(pagamento, dict) else {}
    payment_id = payload.get("payment_id") or pagamento.get("id")

    if evento not in EVENTOS_PAGO or not payment_id:
        return JsonResponse({"ignorado": True})

    mensalidade = (
        Mensalidade.objects.filter(asaas_payment_id=payment_id)
        .exclude(status="paga")
        .first()
    )
    if mensalidade is not None:
        forma = MAPA_FORMA_PAGAMENTO.get(pagamento.get("billingType"), "")
        registrar_pagamento(mensalidade, forma_pagamento=forma)

    return JsonResponse({"ok": True})
