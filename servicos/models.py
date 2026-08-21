from django.db import models
from academias.models import Academia


class Servico(models.Model):
    academia = models.ForeignKey(
        Academia,
        on_delete=models.CASCADE,
        related_name="servicos",
    )

    nome = models.CharField(max_length=150)
    descricao = models.TextField(blank=True)

    valor_padrao = models.DecimalField(
        max_digits=10,
        decimal_places=2,
    )

    dia_vencimento = models.PositiveSmallIntegerField(default=10)

    ativo = models.BooleanField(default=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.nome


class Turma(models.Model):
    academia = models.ForeignKey(
        Academia,
        on_delete=models.CASCADE,
        related_name="turmas",
    )

    servico = models.ForeignKey(
        Servico,
        on_delete=models.PROTECT,
        related_name="turmas",
    )

    nome = models.CharField(max_length=150)
    professor = models.CharField(max_length=150, blank=True)
    dias_semana = models.CharField(max_length=100, blank=True)
    horario = models.TimeField(null=True, blank=True)
    local = models.CharField(max_length=150, blank=True)

    ativo = models.BooleanField(default=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.servico.nome} - {self.nome}"