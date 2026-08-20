from django.db import models
from academias.models import Academia

class Responsavel(models.Model):
    academia = models.ForeignKey(Academia, on_delete=models.CASCADE, related_name="responsaveis")
    nome = models.CharField(max_length=150)
    cpf = models.CharField(max_length=14, unique=True)
    telefone = models.CharField(max_length=20, blank=True)
    whatsapp = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)

    ativo = models.BooleanField(default=True)
    criado = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.nome
# Create your models here.
