from django.contrib import admin
from .models import LembreteCobranca, Mensalidade


@admin.register(Mensalidade)
class MensalidadeAdmin(admin.ModelAdmin):
    list_display = (
        "matricula",
        "competencia",
        "valor",
        "vencimento",
        "status",
        "forma_pagamento",
        "pago_em",
    )

    search_fields = (
        "matricula__atleta__nome",
        "asaas_payment_id",
    )

    list_filter = (
        "academia",
        "status",
        "competencia",
    )

    date_hierarchy = "vencimento"


@admin.register(LembreteCobranca)
class LembreteCobrancaAdmin(admin.ModelAdmin):
    list_display = ("mensalidade", "estagio", "enviado_em")
    list_filter = ("estagio",)
    search_fields = ("mensalidade__matricula__atleta__nome",)
    date_hierarchy = "enviado_em"