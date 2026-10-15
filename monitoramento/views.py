"""Contagens operacionais para Zabbix, sem efeitos financeiros ou HTTP."""
from datetime import timedelta

from django.conf import settings
from django.db.models import Count, Q
from django.http import JsonResponse
from django.utils import timezone
from django.utils.crypto import constant_time_compare
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from academias.models import IntegracaoWhatsApp
from financeiro.models import ContaRecebimento, EventoWebhook, LembreteCobranca, Repasse
from monitoramento.models import AlertaEnviado


def coletar():
    agora = timezone.now()
    repasses = Repasse.objects.exclude(status=Repasse.CONCLUIDA).aggregate(
        repasses_com_falha=Count("pk", filter=(
            Q(status__in=[Repasse.FALHA, Repasse.REQUER_ATENCAO]) | Q(reconciliar=True))),
        repasses_pendentes_30m=Count("pk", filter=Q(
            status__in=[Repasse.PENDENTE, Repasse.PROCESSANDO],
            atualizado_em__lt=agora - timedelta(minutes=30))),
    )
    eventos = EventoWebhook.objects.aggregate(
        webhooks_com_erro=Count("pk", filter=Q(status=EventoWebhook.ERRO)),
        webhooks_pendentes_5m=Count("pk", filter=Q(
            status=EventoWebhook.RECEBIDO, recebido_em__lt=agora - timedelta(minutes=5))),
    )
    return {
        "schema": 1, **repasses, **eventos,
        "contas_com_erro": ContaRecebimento.objects.filter(
            academia__ativo=True, status=ContaRecebimento.ERRO).count(),
        "lembretes_com_erro": LembreteCobranca.objects.filter(
            status=LembreteCobranca.ERRO,
            mensalidade__status__in=["pendente", "vencida"]).count(),
        "whatsapp_desconectados": IntegracaoWhatsApp.objects.filter(
            academia__ativo=True, provider=IntegracaoWhatsApp.PROVIDER_EVOLUTION,
            evolution_instance_name__gt="",
        ).filter(
            Q(status_conexao=IntegracaoWhatsApp.STATUS_ERRO) |
            (Q(ultima_conexao_em__isnull=False) & ~Q(status_conexao=IntegracaoWhatsApp.STATUS_CONECTADO))
        ).count(),
        # Tipos de erro registrados no mecanismo existente, não total de ocorrências
        # nem comprovação de que o Discord recebeu cada tentativa de notificação.
        "tipos_erro_registrados_24h": AlertaEnviado.objects.filter(
            chave__startswith="erro:", ultimo_envio__gte=agora - timedelta(hours=24)).count(),
        "captura_erros_configurada": int(bool(getattr(settings, "ALERTAS_DISCORD_WEBHOOK_URL", ""))),
    }


@never_cache
@require_GET
def metricas(request):
    token = getattr(settings, "MONITORAMENTO_TOKEN", "")
    if len(token) < 32 or not constant_time_compare(
        request.headers.get("Authorization", ""), "Bearer " + token
    ):
        return JsonResponse({"status": "indisponivel"}, status=404)
    try:
        return JsonResponse(coletar())
    except Exception:
        # Não transformar falha da coleta em zeros nem divulgar SQL/credenciais.
        return JsonResponse({"status": "erro"}, status=503)
