from django.db import models

from academias.models import Academia
from atletas.models import Atleta
from servicos.models import Servico, Turma

class Matricula(models.Model):
    academia = models.ForeignKey(
        Academia,
        on_delete=models.CASCADE,
        related_name="matriculas",
    )

    atleta = models.ForeignKey(
        Atleta,
        on_delete=models.CASCADE,
        related_name="matriculas",
    )

    servico = models.ForeignKey(
        Servico,
        on_delete=models.PROTECT,
        related_name="matriculas",
    )

    turma = models.ForeignKey(
        Turma,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="matriculas",

    )

    valor_mensalidade = models.DecimalField(
        max_digits=10,
        decimal_places=2,
    )
    dia_vencimento = models.PositiveSmallIntegerField(default=10)

    data_inicio = models.DateField()
    data_fim = models.DateField(null=True, blank=True)

    ativo = models.BooleanField(default=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.atleta.nome} - {self.servico.nome}"
# Create your models here.
