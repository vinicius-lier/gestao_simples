from django.db import models

# Create your models here.
class Academia(models.Model):
    nome = models.CharField(max_length=150)
    nome_fantasia = models.CharField(max_length=150, blank=True)
    cnpj = models.CharField(max_length=18, unique=True)
    telefone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)

    ativo = models.BooleanField(default=True)
    criado = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.nome

class Unidade(models.Model):
    academia = models.ForeignKey(Academia, on_delete=models.CASCADE, related_name='unidades')
    nome = models.CharField(max_length=150)
    endereco = models.CharField(max_length=250, blank=True)
    telefone = models.CharField(max_length=20, blank=True)
    ativo = models.BooleanField(default=True)

    class Meta:
        ordering = ['nome']
        constraints = [models.UniqueConstraint(fields=['academia', 'nome'], name='unidade_nome_academia')]

    def __str__(self):
        return self.nome
