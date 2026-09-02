from django.core.exceptions import ValidationError
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
    data_fim = models.DateField(
        null=True,
        blank=True,
    )

    ativo = models.BooleanField(default=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    def clean(self):
        erros = {}

        if (
            self.academia_id
            and self.atleta_id
            and self.atleta.academia_id != self.academia_id
        ):
            erros["atleta"] = (
                "O atleta pertence a outra academia."
            )

        if (
            self.academia_id
            and self.servico_id
            and self.servico.academia_id != self.academia_id
        ):
            erros["servico"] = (
                "O serviço pertence a outra academia."
            )

        if self.turma_id:
            if (
                self.academia_id
                and self.turma.academia_id != self.academia_id
            ):
                erros["turma"] = (
                    "A turma pertence a outra academia."
                )

            if (
                self.servico_id
                and self.turma.servico_id != self.servico_id
            ):
                erros["turma"] = (
                    "A turma não pertence ao serviço selecionado."
                )

        if not 1 <= self.dia_vencimento <= 31:
            erros["dia_vencimento"] = (
                "O dia de vencimento deve estar entre 1 e 31."
            )

        if (
            self.data_inicio
            and self.data_fim
            and self.data_fim < self.data_inicio
        ):
            erros["data_fim"] = (
                "A data final não pode ser anterior à data inicial."
            )

        if erros:
            raise ValidationError(erros)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.atleta.nome} - {self.servico.nome}"