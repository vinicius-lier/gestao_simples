import json
import logging

from django.db import transaction
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from financeiro.models import CobrancaPix, ContaRecebimento, EventoWebhook
from django.shortcuts import get_object_or_404
from integracoes.woovi.assinatura import assinatura_valida
from integracoes.woovi.client import WooviClient
from integracoes.woovi.exceptions import WooviError
from integracoes.woovi.services import dados_do_evento, processar_evento

logger = logging.getLogger(__name__)


@csrf_exempt
@require_http_methods(["GET", "POST"])
def webhook(request, conta_id=None):
    """Recebe os avisos da Woovi.

    - OPENPIX:CHARGE_COMPLETED: registra o pagamento; apenas no legado abre
      repasse PENDENTE. O saque nunca acontece nesta requisição: depois de gravar,
      uma thread em segundo plano cuida dele — ver integracoes.woovi.repasses.
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

    chave, correlation_id, resumo = dados
    conta = get_object_or_404(ContaRecebimento, pk=conta_id, modelo_recebimento=ContaRecebimento.CONTA_PROPRIA) if conta_id is not None else None
    cobranca = CobrancaPix.objects.select_related("conta_recebimento").filter(correlation_id=correlation_id).first()
    if conta is not None:
        if (resumo["event"] != "OPENPIX:CHARGE_COMPLETED" or cobranca is None
                or cobranca.conta_recebimento_id != conta.pk
                or cobranca.modelo_recebimento != ContaRecebimento.CONTA_PROPRIA):
            return JsonResponse({"detail": "origem incompatível"}, status=400)
    elif cobranca is not None and cobranca.modelo_recebimento == ContaRecebimento.CONTA_PROPRIA:
        return JsonResponse({"detail": "use o webhook da conta"}, status=400)

    fatura = None
    if conta is None and resumo["event"] == "OPENPIX:CHARGE_COMPLETED" and correlation_id.startswith("assinatura-"):
        from assinaturas.services import fatura_do_correlation_id
        fatura = fatura_do_correlation_id(correlation_id)
    base = (conta.api_base_url if conta is not None else
            fatura.api_base_url if fatura is not None else
            cobranca.conta_recebimento.api_base_url if cobranca is not None else "")
    opcoes = {"base_url": base} if base else {}
    if not assinatura_valida(corpo, request.headers.get("x-webhook-signature", ""), **opcoes):
        logger.warning("Webhook Woovi %s recusado: assinatura inválida.", payload.get("event"))
        return JsonResponse({"detail": "assinatura inválida"}, status=401)

    if EventoWebhook.objects.filter(chave=chave, status__in=(EventoWebhook.PROCESSADO, EventoWebhook.IGNORADO)).exists():
        return JsonResponse({"ok": True, "duplicado": True})

    # A assinatura RSA é da Woovi, não de uma empresa específica. Confirme
    # a transação pela credencial de origem em qualquer cobrança conhecida:
    # outra empresa pode emitir a mesma correlationID, inclusive de legado.
    if cobranca is not None or fatura is not None:
        try:
            origem = (WooviClient(conta=cobranca.conta_recebimento) if cobranca is not None else WooviClient(
                contexto="plataforma", credencial_ref=fatura.credencial_ref,
                base_url=fatura.api_base_url or None,
            ))
            remota = origem.obter_cobranca(correlation_id)
        except WooviError:
            return JsonResponse({"detail": "não foi possível confirmar a origem"}, status=503)
        if (remota.correlation_id != correlation_id or remota.status != "COMPLETED"
                or not remota.transaction_id):
            return JsonResponse({"detail": "pagamento ainda não confirmado na origem"}, status=503)
        if remota.transaction_id != resumo["charge"].get("transactionID"):
            return JsonResponse({"detail": "transação incompatível com a origem"}, status=400)

    evento, _criado = EventoWebhook.objects.get_or_create(
        chave=chave,
        defaults={"tipo": resumo["event"], "correlation_id": correlation_id, "payload": resumo},
    )
    with transaction.atomic():
        evento = EventoWebhook.objects.select_for_update().get(pk=evento.pk)
        if evento.status in (EventoWebhook.PROCESSADO, EventoWebhook.IGNORADO):
            return JsonResponse({"ok": True, "duplicado": True})
        processar_evento(evento)

    return JsonResponse({"ok": True})
