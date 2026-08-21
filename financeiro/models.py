from django.db import models

from academias.models import Academia
from matriculas.models import Matricula


class Mensalidade(models.Model):
    STATUS = [
        ("pendente", "Pendente"),
        ("paga", "Paga"),
        ("vencida", "Vencida"),
        ("cancelada", "Cancelada"),
        ("isenta", "Isenta"),
    ]

    academia = models.ForeignKey(
        Academia,
        on_delete=models.CASCADE,
        related_name="mensalidades",
    )

    matricula = models.ForeignKey(
        Matricula,
        on_delete=models.CASCADE,
        related_name="mensalidades",
    )

    competencia = models.DateField()

    valor = models.DecimalField(
        max_digits=10,
        decimal_places=2,
    )

    vencimento = models.DateField()

    status = models.CharField(
        max_length=20,
        choices=STATUS,
        default="pendente",
    )

    pago_em = models.DateTimeField(
        null=True,
        blank=True,
    )

    asaas_payment_id = models.CharField(
        max_length=100,
        blank=True,
    )

    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["matricula", "competencia"],
                name="mensalidade_unica_por_competencia",
            )
        ]

    def __str__(self):
        return f"{self.matricula.atleta.nome} - {self.competencia:%m/%Y}"