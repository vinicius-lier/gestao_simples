from django.contrib import admin
from .models import AcessoAcademia


@admin.register(AcessoAcademia)
class AcessoAcademiaAdmin(admin.ModelAdmin):
    list_display = ('usuario', 'academia')
    search_fields = ('usuario__username', 'academia__nome')

    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    has_add_permission = has_view_permission
    has_change_permission = has_view_permission
    has_delete_permission = has_view_permission
