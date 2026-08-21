from django.contrib import admin
from .models import Servico, Turma


@admin.register(Servico)
class ServicoAdmin(admin.ModelAdmin):
    list_display = (
        "nome",
        "academia",
        "valor_padrao",
        "dia_vencimento",
        "ativo",
    )

    search_fields = (
        "nome",
        "academia__nome",
    )

    list_filter = (
        "academia",
        "ativo",
    )


@admin.register(Turma)
class TurmaAdmin(admin.ModelAdmin):
    list_display = (
        "nome",
        "academia",
        "servico",
        "professor",
        "dias_semana",
        "horario",
        "ativo",
    )

    search_fields = (
        "nome",
        "professor",
    )

    list_filter = (
        "academia",
        "servico",
        "ativo",
    )