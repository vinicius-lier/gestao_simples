from django.contrib import admin

from .models import Assinatura, FaturaAssinatura
from .services import confirmar_pagamento, gerar_faturas


@admin.register(Assinatura)
class AssinaturaAdmin(admin.ModelAdmin):
    list_display = ("academia", "plano", "valor_mensal", "dia_vencimento", "inicio", "ativa")
    list_filter = ("ativa",)
    actions = ["gerar_faturas_agora"]

    @admin.action(description="Gerar agora a fatura do mês (se ainda não existir)")
    def gerar_faturas_agora(self, request, queryset):
        self.message_user(request, f"{gerar_faturas()} fatura(s) criada(s).")


@admin.register(FaturaAssinatura)
class FaturaAssinaturaAdmin(admin.ModelAdmin):
    list_display = ("assinatura", "competencia", "valor", "vencimento", "status", "pagamento_informado_em", "pago_em")
    list_filter = ("status", "assinatura__academia")
    date_hierarchy = "vencimento"
    readonly_fields = ("pagamento_informado_em", "pago_em", "criada_em")
    actions = ["confirmar", "cancelar"]

    @admin.action(description="Confirmar pagamento (o Pix chegou)")
    def confirmar(self, request, queryset):
        faturas = queryset.filter(status=FaturaAssinatura.ABERTA)
        for fatura in faturas:
            confirmar_pagamento(fatura)
        self.message_user(request, f"{len(faturas)} fatura(s) confirmada(s) como paga(s).")

    @admin.action(description="Cancelar fatura")
    def cancelar(self, request, queryset):
        total = queryset.filter(status=FaturaAssinatura.ABERTA).update(status=FaturaAssinatura.CANCELADA)
        self.message_user(request, f"{total} fatura(s) cancelada(s).")
