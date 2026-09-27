from django.contrib import admin

from .models import Academia, IntegracaoWhatsApp

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


@admin.register(IntegracaoWhatsApp)
class IntegracaoWhatsAppAdmin(admin.ModelAdmin):
    list_display = (
        "academia",
        "provider",
        "numero_whatsapp",
        "status_conexao",
        "ultima_conexao_em",
    )
    list_filter = (
        "provider",
        "status_conexao",
        "academia",
    )
    search_fields = (
        "academia__nome",
        "evolution_instance_name",
        "numero_whatsapp",
    )
    readonly_fields = (
        "status_conexao",
        "ultimo_status",
        "ultimo_status_em",
        "ultima_conexao_em",
        "criado_em",
        "atualizado_em",
    )

    def get_queryset(self, request):
        """Isolamento por academia: quem não é superusuário só enxerga as
        integrações das academias a que tem acesso (AcessoAcademia)."""
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        from portal.models import AcessoAcademia

        academias = AcessoAcademia.objects.filter(
            usuario=request.user
        ).values_list("academia_id", flat=True)
        return qs.filter(academia_id__in=academias)