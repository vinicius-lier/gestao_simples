from django.contrib import admin
from .models import Matricula


@admin.register(Matricula)
class MatriculaAdmin(admin.ModelAdmin):
    list_display = (
        "atleta",
        "academia",
        "servico",
        "turma",
        "valor_mensalidade",
        "dia_vencimento",
        "ativo",
    )

    search_fields = (
        "atleta__nome",
        "servico__nome",
    )

    list_filter = (
        "academia",
        "servico",
        "turma",
        "ativo",
    )