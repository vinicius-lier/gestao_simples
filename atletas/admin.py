from django.contrib import admin
from .models import Responsavel, Atleta


@admin.register(Responsavel)
class ResponsavelAdmin(admin.ModelAdmin):
    list_display = (
        "nome",
        "academia",
        "whatsapp",
        "ativo",
    )

    search_fields = (
        "nome",
        "cpf",
        "whatsapp",
    )

    list_filter = (
        "academia",
        "ativo",
    )


@admin.register(Atleta)
class AtletaAdmin(admin.ModelAdmin):
    list_display = (
        "nome",
        "academia",
        "responsavel_financeiro",
        "faixa",
        "status",
    )

    search_fields = (
        "nome",
        "cpf",
        "responsavel_financeiro__nome",
    )

    list_filter = (
        "academia",
        "status",
        "faixa",
    )