"""Apaga do disco a foto e os documentos quando o registro some (aluno
excluído, documento removido). Só depois do commit: se a transação desfizer
a exclusão, o arquivo continua lá."""
from django.db import transaction
from django.db.models.signals import post_delete
from django.dispatch import receiver

from .models import Atleta, DocumentoAtleta


def _apagar_depois(storage, nome):
    if not nome:
        return

    def apagar():
        try:
            storage.delete(nome)
        except OSError:
            pass

    transaction.on_commit(apagar)


@receiver(post_delete, sender=Atleta)
def apagar_foto(sender, instance, **kwargs):
    if instance.foto:
        _apagar_depois(instance.foto.storage, instance.foto.name)


@receiver(post_delete, sender=DocumentoAtleta)
def apagar_documento(sender, instance, **kwargs):
    if instance.arquivo:
        _apagar_depois(instance.arquivo.storage, instance.arquivo.name)
