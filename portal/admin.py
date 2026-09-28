from django.contrib import admin
from .models import AcessoAcademia


@admin.register(AcessoAcademia)
class AcessoAcademiaAdmin(admin.ModelAdmin):
    list_display = ('usuario', 'academia', 'administrador')
    search_fields = ('usuario__username', 'academia__nome')

    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    has_add_permission = has_view_permission
    has_change_permission = has_view_permission
    has_delete_permission = has_view_permission


from .models import PaginaPublica, FotoPublica


@admin.register(PaginaPublica)
class PaginaPublicaAdmin(AcessoAcademiaAdmin):
    list_display = ('titulo',)
    search_fields = ('titulo',)

    def has_add_permission(self, request):
        return request.user.is_superuser and not PaginaPublica.objects.exists()


@admin.register(FotoPublica)
class FotoPublicaAdmin(AcessoAcademiaAdmin):
    list_display = ('titulo', 'ordem', 'publicada')
    search_fields = ('titulo', 'legenda')
    list_filter = ('publicada',)


from .models import AulaExperimental, ConfiguracaoExperimental, InscricaoExperimental


@admin.register(ConfiguracaoExperimental)
class ConfiguracaoExperimentalAdmin(AcessoAcademiaAdmin):
    list_display = ('academia', 'ativo', 'vagas_padrao', 'janela_dias', 'fila_habilitada_padrao')
    list_filter = ('ativo',)


class InscricaoExperimentalInline(admin.TabularInline):
    model = InscricaoExperimental
    extra = 0
    fields = ('nome', 'idade', 'responsavel', 'telefone', 'email', 'status', 'criado_em')
    readonly_fields = ('criado_em',)


@admin.register(AulaExperimental)
class AulaExperimentalAdmin(AcessoAcademiaAdmin):
    list_display = ('turma', 'inicio', 'vagas', 'ocupadas', 'fila_habilitada', 'ativa')
    list_filter = ('ativa', 'fila_habilitada')
    search_fields = ('turma__nome', 'turma__modalidade__nome')
    inlines = [InscricaoExperimentalInline]


@admin.register(InscricaoExperimental)
class InscricaoExperimentalAdmin(AcessoAcademiaAdmin):
    list_display = ('nome', 'aula', 'status', 'telefone', 'criado_em')
    list_filter = ('status',)
    search_fields = ('nome', 'telefone', 'email')


from .models import ConviteMatricula


@admin.register(ConviteMatricula)
class ConviteMatriculaAdmin(AcessoAcademiaAdmin):
    list_display = ('__str__', 'academia', 'status', 'criado_em', 'expira_em')
    list_filter = ('status', 'academia')
    search_fields = ('convidado_nome', 'atleta__nome', 'token')
    readonly_fields = ('token', 'criado_em', 'preenchido_em', 'ativado_em', 'atleta', 'matricula')

    def has_add_permission(self, request):
        return False


from .models import FichaMatricula


@admin.register(FichaMatricula)
class FichaMatriculaAdmin(AcessoAcademiaAdmin):
    list_display = ('nome_aluno', 'atleta', 'academia', 'origem', 'respondida_em')
    list_filter = ('origem', 'academia')
    search_fields = ('nome_aluno', 'atleta__nome', 'telefone_contato', 'email_contato')
    readonly_fields = ('academia', 'atleta', 'convite', 'origem', 'respondida_em', 'criado_em')

    def has_add_permission(self, request):
        return False


from .models import TokenAcessoResponsavel


@admin.register(TokenAcessoResponsavel)
class TokenAcessoResponsavelAdmin(AcessoAcademiaAdmin):
    # Somente leitura: o token é sensível e a criação/consumo acontece
    # sempre pelo fluxo da aplicação, nunca à mão.
    list_display = ('responsavel', 'criado_em', 'expira_em', 'usado_em')
    search_fields = ('responsavel__nome',)
    exclude = ('token',)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
