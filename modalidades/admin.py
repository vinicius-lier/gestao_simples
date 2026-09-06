from django.contrib import admin
from .models import Modalidade, Turma, Graduacao


@admin.register(Modalidade)
class ModalidadeAdmin(admin.ModelAdmin):
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
        "modalidade",
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
        "modalidade",
        "ativo",
    )


@admin.register(Graduacao)
class GraduacaoAdmin(admin.ModelAdmin):
    list_display = ("nome", "modalidade", "ordem", "academia", "ativo")
    list_filter = ("academia", "modalidade")
