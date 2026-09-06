from django.conf import settings
from django.db import models


class AcessoAcademia(models.Model):
    usuario = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    academia = models.ForeignKey('academias.Academia', on_delete=models.CASCADE)

    class Meta:
        verbose_name = 'acesso à academia'
        verbose_name_plural = 'acessos às academias'

    def __str__(self):
        return f'{self.usuario} — {self.academia}'
