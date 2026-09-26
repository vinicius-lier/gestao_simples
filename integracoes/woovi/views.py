import json
import logging

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from integracoes.woovi.client import WooviAPIError
from integracoes.woovi.services import confirmar_pagamento_pix

logger = logging.getLogger(__name__)

EVENTO_PAGO = "OPENPIX:CHARGE_COMPLETED"


@csrf_exempt
@require_http_methods(["GET", "POST"])
def webhook_pagamento(request):
    """Recebe o aviso da Woovi de que um Pix foi pago e dá baixa na
    mensalidade — sem conferência manual.

    Segurança: o corpo do webhook NÃO é tomado como verdade. Ele só informa
    o correlationID; ``confirmar_pagamento_pix`` consulta a cobrança na API
    da Woovi com o nosso AppID e só dá baixa se ela estiver COMPLETED. Um
    POST forjado, no máximo, provoca uma consulta que não muda nada.

    O cadastro do webhook no painel da Woovi faz um POST de teste
    (``{"data_criacao": ..., "event": ...}``, sem cobrança) e espera 200.
    """
    if request.method == "GET":
        return JsonResponse({"status": "ok"})

    try:
        payload = json.loads(request.body or b"{}")
    except (TypeError, ValueError):
        return JsonResponse({"detail": "corpo inválido"}, status=400)

    if not isinstance(payload, dict):
        return JsonResponse({"detail": "corpo inválido"}, status=400)

    cobranca = payload.get("charge")
    cobranca = cobranca if isinstance(cobranca, dict) else {}
    correlation_id = cobranca.get("correlationID")

    if payload.get("event") != EVENTO_PAGO or not correlation_id:
        return JsonResponse({})

    try:
        confirmar_pagamento_pix(correlation_id)
    except (ValueError, WooviAPIError) as exc:
        # Woovi fora do ar ou AppID não configurado: responde erro para a
        # Woovi reenviar. A rotina diária também confere os Pix em aberto
        # antes de mandar lembrete (financeiro.lembretes), então um aviso
        # perdido não vira cobrança indevida.
        logger.error("Woovi: não foi possível conferir o Pix %s: %s", correlation_id, exc)
        return JsonResponse({"detail": "falha ao conferir o pagamento"}, status=503)

    return JsonResponse({"ok": True})
