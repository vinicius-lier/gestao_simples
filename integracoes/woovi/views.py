import json
import logging

from django.db import transaction
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from financeiro.models import EventoWebhook
from integracoes.woovi.assinatura import assinatura_valida
from integracoes.woovi.services import dados_do_evento, processar_evento

logger = logging.getLogger(__name__)


@csrf_exempt
@require_http_methods(["GET", "POST"])
def webhook(request):
    """Recebe os avisos da Woovi.

    - OPENPIX:CHARGE_COMPLETED: registra o pagamento e abre o repasse
      PENDENTE. O saque NUNCA acontece aqui — ver integracoes.woovi.repasses.
    - OPENPIX:MOVEMENT_CONFIRMED / MOVEMENT_FAILED: atualizam o repasse.

    Segurança: todo evento que muda algo exige ``x-webhook-signature``
    válido (RSA-SHA256 da Woovi sobre o corpo bruto). Outros eventos e o
    POST de teste do cadastro (sem cobrança) recebem 200 sem efeito.

    Idempotência: o evento é gravado com uma chave única antes de ser
    processado; repetição já processada responde 200 sem efeito. Regras de
    negócio conhecidas nunca viram 500.
    """
    if request.method == "GET":
        return JsonResponse({"status": "ok"})

    corpo = request.body
    try:
        payload = json.loads(corpo or b"{}")
    except (TypeError, ValueError):
        return JsonResponse({"detail": "corpo inválido"}, status=400)
    if not isinstance(payload, dict):
        return JsonResponse({"detail": "corpo inválido"}, status=400)

    dados = dados_do_evento(payload)
    if dados is None:
        return JsonResponse({})

    if not assinatura_valida(corpo, request.headers.get("x-webhook-signature", "")):
        logger.warning("Webhook Woovi %s recusado: assinatura inválida.", payload.get("event"))
        return JsonResponse({"detail": "assinatura inválida"}, status=401)

    chave, correlation_id, resumo = dados
    evento, _criado = EventoWebhook.objects.get_or_create(
        chave=chave,
        defaults={"tipo": resumo["event"], "correlation_id": correlation_id, "payload": resumo},
    )
    if evento.status in (EventoWebhook.PROCESSADO, EventoWebhook.IGNORADO):
        return JsonResponse({"ok": True, "duplicado": True})

    with transaction.atomic():
        evento = EventoWebhook.objects.select_for_update().get(pk=evento.pk)
        if evento.status in (EventoWebhook.PROCESSADO, EventoWebhook.IGNORADO):
            return JsonResponse({"ok": True, "duplicado": True})
        processar_evento(evento)

    return JsonResponse({"ok": True})
