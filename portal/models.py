import secrets
from .models_experimentais import (
    AulaExperimental,
    ConfiguracaoExperimental,
    InscricaoExperimental,
)
from .models_matricula import ConviteMatricula
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone


class AcessoAcademia(models.Model):
    administrador = models.BooleanField(default=False, verbose_name='administrador da academia')
    usuario = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    academia = models.ForeignKey('academias.Academia', on_delete=models.CASCADE)

    class Meta:
        verbose_name = 'acesso à academia'
        verbose_name_plural = 'acessos às academias'

    def __str__(self):
        return f'{self.usuario} — {self.academia}'


class PaginaPublica(models.Model):
    titulo = models.CharField(max_length=150, default='Escola de Judô Keiko Fukuda')
    historia = models.TextField(blank=True, help_text='História oficial da escola. Deixe vazio enquanto não houver conteúdo aprovado.')
    endereco = models.TextField(blank=True)
    contato = models.CharField(max_length=150, blank=True, help_text='Telefone ou WhatsApp para exibir na página.')
    chamada = models.CharField(max_length=150, blank=True, help_text='Título da divulgação atual.')
    divulgacao = models.TextField(blank=True)

    class Meta:
        verbose_name = 'página pública'
        verbose_name_plural = 'página pública'

    def __str__(self):
        return self.titulo


class TokenAcessoResponsavel(models.Model):
    """Link de acesso ao portal do responsável — sem senha. O staff gera
    (ou, no futuro, o próprio sistema via API do WhatsApp) e envia; um
    clique válido abre uma sessão comum no navegador do responsável,
    que dura o tempo padrão de sessão do Django. Token de uso único."""

    responsavel = models.ForeignKey(
        'atletas.Responsavel', on_delete=models.CASCADE, related_name='tokens_acesso'
    )
    token = models.CharField(max_length=64, unique=True, editable=False)
    criado_em = models.DateTimeField(auto_now_add=True)
    expira_em = models.DateTimeField()
    usado_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'link de acesso do responsável'
        verbose_name_plural = 'links de acesso dos responsáveis'

    def __str__(self):
        return f'{self.responsavel} — {self.criado_em:%d/%m/%Y %H:%M}'

    @classmethod
    def gerar(cls, responsavel, validade_horas=24):
        return cls.objects.create(
            responsavel=responsavel,
            token=secrets.token_urlsafe(32),
            expira_em=timezone.now() + timedelta(hours=validade_horas),
        )

    @property
    def valido(self):
        return self.usado_em is None and self.expira_em > timezone.now()

    def consumir(self):
        self.usado_em = timezone.now()
        self.save(update_fields=['usado_em'])


class FotoPublica(models.Model):
    titulo = models.CharField(max_length=150)
    url = models.URLField(max_length=1000, help_text='URL HTTPS direta de uma foto autorizada para divulgação pública.')
    legenda = models.CharField(max_length=250, blank=True)
    ordem = models.PositiveIntegerField(default=0)
    publicada = models.BooleanField(default=False)

    class Meta:
        ordering = ['ordem', 'pk']
        verbose_name = 'foto pública'
        verbose_name_plural = 'fotos públicas'

    def __str__(self):
        return self.titulo
