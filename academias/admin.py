from django.contrib import admin
from .models import Academia

@admin.register(Academia)
class AcademiaAdmin(admin.ModelAdmin):
    list_display = (
        "nome",
        "nome_fantasia",
        "cnpj",
        "telefone",
        "ativo",
    )

    search_fields = (
        "nome",
        "nome_fantasia",
        "cnpj",
    )

    list_filter = (
        "ativo",
    )
    