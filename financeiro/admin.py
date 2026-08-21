from django.contrib import admin
from .models import Mensalidade


@admin.register(Mensalidade)
class MensalidadeAdmin(admin.ModelAdmin):
    list_display = (
        "matricula",
        "competencia",
        "valor",
        "vencimento",
        "status",
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