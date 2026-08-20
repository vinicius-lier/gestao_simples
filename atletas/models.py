from django.db import models
from academias.models import Academia


class Responsavel(models.Model):
    academia = models.ForeignKey(
        Academia,
        on_delete=models.CASCADE,
        related_name="responsaveis",
    )

    nome = models.CharField(max_length=150)
    cpf = models.CharField(max_length=14, blank=True)
    telefone = models.CharField(max_length=20, blank=True)
    whatsapp = models.CharField(max_length=20)
    email = models.EmailField(blank=True)

    ativo = models.BooleanField(default=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.nome


class Atleta(models.Model):
    STATUS = [
        ("ativo", "Ativo"),
        ("inativo", "Inativo"),
        ("trancado", "Trancado"),
    ]

    academia = models.ForeignKey(
        Academia,
        on_delete=models.CASCADE,
        related_name="atletas",
    )

    responsavel_financeiro = models.ForeignKey(
        Responsavel,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="atletas",
    )

    nome = models.CharField(max_length=150)
    data_nascimento = models.DateField()
    cpf = models.CharField(max_length=14, blank=True)
    faixa = models.CharField(max_length=50, blank=True)

    status = models.CharField(
        max_length=20,
        choices=STATUS,
        default="ativo",
    )

    observacoes = models.TextField(blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.nome