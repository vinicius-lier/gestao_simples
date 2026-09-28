from django.contrib import admin

from .models import Assinatura, FaturaAssinatura
from .services import atualizar_situacao, cancelar_fatura, confirmar_pagamento_manual, rotina_diaria


@admin.register(Assinatura)
class AssinaturaAdmin(admin.ModelAdmin):
    list_display = ("academia", "plano", "valor_mensal", "dia_vencimento", "primeiro_vencimento", "status")
    list_filter = ("status",)
    readonly_fields = ("motivo_suspensao", "suspensa_em", "criada_em")
    actions = ["rodar_rotina_agora", "reativar"]

    @admin.action(description="Rodar agora a rotina do dia (gerar fatura, atraso e suspensão)")
    def rodar_rotina_agora(self, request, queryset):
        self.message_user(request, f"{rotina_diaria()} fatura(s) criada(s); situação das assinaturas atualizada.")

    @admin.action(description="Reativar (suspensão manual ou depois de acerto por fora)")
    def reativar(self, request, queryset):
        total = queryset.filter(status=Assinatura.SUSPENSA).update(
            status=Assinatura.ATIVA, motivo_suspensao="", suspensa_em=None,
        )
        for assinatura in queryset:
            # Se ainda houver fatura fora da tolerância, a rotina suspende de novo.
            atualizar_situacao(assinatura)
        self.message_user(request, f"{total} assinatura(s) reativada(s).")


@admin.register(FaturaAssinatura)
class FaturaAssinaturaAdmin(admin.ModelAdmin):
    list_display = ("assinatura", "tipo", "competencia", "valor", "vencimento", "status", "pago_em")
    list_filter = ("status", "tipo", "assinatura__academia")
    date_hierarchy = "vencimento"
    readonly_fields = ("correlation_id", "woovi_charge_id", "br_code", "pix_expira_em", "criada_em", "atualizada_em")
    actions = ["confirmar", "cancelar"]

    @admin.action(description="Confirmar pagamento recebido por fora do Pix")
    def confirmar(self, request, queryset):
        total = sum(confirmar_pagamento_manual(fatura) for fatura in queryset)
        self.message_user(request, f"{total} fatura(s) confirmada(s) como paga(s).")

    @admin.action(description="Cancelar fatura")
    def cancelar(self, request, queryset):
        total = sum(cancelar_fatura(fatura) for fatura in queryset)
        self.message_user(request, f"{total} fatura(s) cancelada(s).")
