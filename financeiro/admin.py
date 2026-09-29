from django.contrib import admin
from .models import CobrancaPix, ContaRecebimento, EventoWebhook, LembreteCobranca, Mensalidade, Repasse


@admin.register(Mensalidade)
class MensalidadeAdmin(admin.ModelAdmin):
    list_display = (
        "matricula",
        "tipo",
        "competencia",
        "valor",
        "vencimento",
        "status",
        "forma_pagamento",
        "pago_em",
    )

    search_fields = (
        "matricula__atleta__nome",
        "cobrancas_pix__correlation_id",
    )

    list_filter = (
        "academia",
        "tipo",
        "status",
        "competencia",
    )

    date_hierarchy = "vencimento"


@admin.register(LembreteCobranca)
class LembreteCobrancaAdmin(admin.ModelAdmin):
    list_display = (
        "mensalidade",
        "estagio",
        "status",
        "tentativas",
        "provider",
        "enviado_em",
    )
    list_filter = ("estagio", "status", "provider")
    search_fields = ("mensalidade__matricula__atleta__nome",)
    date_hierarchy = "enviado_em"
    readonly_fields = ("atualizado_em",)

@admin.register(ContaRecebimento)
class ContaRecebimentoAdmin(admin.ModelAdmin):
    list_display = ("academia", "modelo_recebimento", "status", "pix_key", "ativa", "criada_em", "desativada_em")
    list_filter = ("modelo_recebimento", "status", "ativa", "academia")
    search_fields = ("pix_key", "academia__nome")
    readonly_fields = tuple(f.name for f in ContaRecebimento._meta.fields if f.name not in ("id", "credencial_fingerprint", "onboarding_url"))
    exclude = ("credencial_fingerprint", "onboarding_url")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False  # histórico financeiro


@admin.register(CobrancaPix)
class CobrancaPixAdmin(admin.ModelAdmin):
    list_display = ("mensalidade", "status", "valor", "conta_recebimento", "expira_em", "pago_em")
    list_filter = ("status", "modelo_recebimento")
    search_fields = ("correlation_id", "transaction_id", "mensalidade__matricula__atleta__nome")
    readonly_fields = ("mensalidade", "conta_recebimento", "modelo_recebimento", "correlation_id", "criada_em", "atualizada_em")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(EventoWebhook)
class EventoWebhookAdmin(admin.ModelAdmin):
    list_display = ("tipo", "correlation_id", "status", "erro", "recebido_em", "processado_em")
    list_filter = ("tipo", "status")
    search_fields = ("correlation_id", "chave")
    readonly_fields = ("chave", "tipo", "correlation_id", "payload", "status", "erro", "recebido_em", "processado_em")


@admin.register(Repasse)
class RepasseAdmin(admin.ModelAdmin):
    list_display = ("academia", "status", "valor", "pix_key_destino", "tentativas", "proxima_tentativa_em", "erro", "criado_em")
    list_filter = ("status", "academia")
    search_fields = ("correlation_id", "end_to_end_id", "pix_key_destino")
    readonly_fields = ("criado_em", "processado_em", "concluido_em", "atualizado_em")
    actions = ["tentar_de_novo"]

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.action(description="Tentar o repasse de novo agora (após corrigir a causa)")
    def tentar_de_novo(self, request, queryset):
        from django.db import IntegrityError, transaction
        from django.utils import timezone

        reabertos = 0
        for repasse in queryset.filter(status=Repasse.REQUER_ATENCAO, conta_recebimento__modelo_recebimento=ContaRecebimento.LEGADO_SUBCONTA):
            try:
                with transaction.atomic():
                    Repasse.objects.filter(pk=repasse.pk).update(
                        status=Repasse.PENDENTE, tentativas=0, proxima_tentativa_em=timezone.now(),
                    )
                reabertos += 1
            except IntegrityError:
                self.message_user(
                    request, f"Repasse {repasse.pk}: já existe outro repasse aberto para esta conta.", level="warning",
                )
        self.message_user(request, f"{reabertos} repasse(s) reaberto(s).")
