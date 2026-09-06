from django.conf import settings
from django.db import models


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
