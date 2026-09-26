import json
import logging

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from financeiro.models import Mensalidade
from financeiro.services import registrar_pagamento

logger = logging.getLogger(__name__)

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
@require_http_methods(["GET", "POST"])
def webhook_pagamento(request):
    """Recebe a notificação do Asaas quando um pagamento é confirmado e
    marca a mensalidade correspondente como paga — sem precisar de
    conferência manual.

    GET/HEAD respondem 200 sem exigir token: o próprio formulário de
    cadastro de webhook do Asaas faz uma checagem de alcançabilidade da
    URL antes de salvar, e rejeita (“url inválida”) se não vier 2xx.

    Segurança: configure ASAAS_WEBHOOK_TOKEN (o mesmo token cadastrado no
    painel do Asaas ao criar o webhook) para exigir o cabeçalho
    'asaas-access-token' em todo POST. Sem o token configurado, o
    endpoint aceita qualquer chamada — use isso só em desenvolvimento.
    """
    if request.method == "GET":
        return JsonResponse({"status": "ok"})

    token_esperado = getattr(settings, "ASAAS_WEBHOOK_TOKEN", "")
    exige_token = getattr(
        settings, "ASAAS_WEBHOOK_REQUIRE_TOKEN", not settings.DEBUG
    )

    if exige_token and not token_esperado:
        # Produção sem token configurado: recusa eventos em vez de aceitar
        # qualquer chamada. Ver settings.ASAAS_WEBHOOK_REQUIRE_TOKEN.
        return JsonResponse(
            {"detail": "webhook do Asaas não configurado"}, status=503
        )

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
        try:
            registrar_pagamento(mensalidade, forma_pagamento=forma)
        except ValueError:
            # Pagamento de mensalidade cancelada: responde 2xx mesmo assim.
            # Erro aqui faria o Asaas reenviar sem parar e, com falhas
            # seguidas, pausar a fila — travando a baixa de TODOS os pagamentos.
            logger.warning(
                "Asaas: pagamento %s recebido para a mensalidade %s, que está cancelada. "
                "Conferir e estornar/regularizar manualmente.",
                payment_id,
                mensalidade.pk,
            )
            return JsonResponse({"ignorado": True, "motivo": "mensalidade cancelada"})

    return JsonResponse({"ok": True})
