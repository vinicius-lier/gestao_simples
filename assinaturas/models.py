from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

from academias.models import Academia


class Assinatura(models.Model):
    """O que a academia paga pelo uso do sistema. Cadastrada pela equipe da
    plataforma no admin; a academia só consulta e paga (Minha assinatura)."""

    academia = models.OneToOneField(Academia, on_delete=models.CASCADE, related_name="assinatura")
    plano = models.CharField(max_length=80, default="Gestão Simples")
    valor_mensal = models.DecimalField("valor mensal (R$)", max_digits=10, decimal_places=2)
    dia_vencimento = models.PositiveSmallIntegerField(
        "dia de vencimento", default=10, validators=[MinValueValidator(1), MaxValueValidator(28)],
    )
    inicio = models.DateField("início da cobrança", help_text="A 1ª fatura é a do primeiro vencimento a partir desta data.")
    ativa = models.BooleanField(default=True)
    criada_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "assinatura"
        verbose_name_plural = "assinaturas"

    def __str__(self):
        return f"{self.academia} — {self.plano}"


class FaturaAssinatura(models.Model):
    ABERTA = "aberta"
    PAGA = "paga"
    CANCELADA = "cancelada"
    STATUS = [(ABERTA, "Em aberto"), (PAGA, "Paga"), (CANCELADA, "Cancelada")]

    assinatura = models.ForeignKey(Assinatura, on_delete=models.CASCADE, related_name="faturas")
    competencia = models.DateField()
    valor = models.DecimalField(max_digits=10, decimal_places=2)
    vencimento = models.DateField()
    status = models.CharField(max_length=10, choices=STATUS, default=ABERTA)
    pagamento_informado_em = models.DateTimeField(
        null=True, blank=True, help_text="Quando a academia avisou, pelo portal, que já pagou.",
    )
    pago_em = models.DateTimeField(null=True, blank=True, help_text="Quando a plataforma confirmou o pagamento.")
    criada_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-competencia"]
        verbose_name = "fatura da assinatura"
        verbose_name_plural = "faturas da assinatura"
        constraints = [
            models.UniqueConstraint(fields=["assinatura", "competencia"], name="fatura_assinatura_unica_por_mes"),
        ]

    def __str__(self):
        return f"{self.assinatura.academia} — {self.competencia:%m/%Y}"

    @property
    def vencida(self):
        return self.status == self.ABERTA and self.vencimento < timezone.localdate()

    @property
    def situacao(self):
        """Como a academia vê a fatura."""
        if self.status == self.PAGA:
            return "Paga"
        if self.status == self.CANCELADA:
            return "Cancelada"
        if self.pagamento_informado_em:
            return "Pagamento informado — aguardando confirmação"
        return "Vencida" if self.vencida else "Em aberto"

    @property
    def txid(self):
        """Identificador que vai no Pix (até 25 letras e números): mostra no
        extrato de qual fatura é o pagamento."""
        return f"ASSIN{self.pk:06d}"
