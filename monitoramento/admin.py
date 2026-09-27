from django.contrib import admin

from .models import AlertaEnviado


@admin.register(AlertaEnviado)
class AlertaEnviadoAdmin(admin.ModelAdmin):
    list_display = ("chave", "ultimo_envio", "repeticoes")
    search_fields = ("chave",)
    readonly_fields = ("chave", "ultimo_envio", "repeticoes")

    def has_add_permission(self, request):
        return False
