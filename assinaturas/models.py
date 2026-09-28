from datetime import timedelta

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone

from academias.models import Academia


class Assinatura(models.Model):
    """O contrato da academia com a plataforma: o que ela paga pelo uso do
    sistema, todo mês. Cadastrada pela equipe da plataforma no admin; a
    academia consulta e paga em Configurações → Minha assinatura.

    Cada mês vira uma ``FaturaAssinatura``, cobrada por Pix pela Woovi (na
    conta da plataforma). O histórico financeiro fica aqui no banco; a Woovi
    só processa a cobrança e avisa o pagamento pelo webhook."""

    ATIVA = "ativa"
    SUSPENSA = "suspensa"
    CANCELADA = "cancelada"
    STATUS = [(ATIVA, "Ativa"), (SUSPENSA, "Suspensa"), (CANCELADA, "Cancelada")]

    INADIMPLENCIA = "inadimplencia"
    MANUAL = "manual"
    MOTIVOS_SUSPENSAO = [(INADIMPLENCIA, "Mensalidade em atraso"), (MANUAL, "Suspensa pela plataforma")]

    WOOVI = "woovi"
    GATEWAYS = [(WOOVI, "Pix (Woovi)")]

    academia = models.OneToOneField(Academia, on_delete=models.CASCADE, related_name="assinatura")
    plano = models.CharField(max_length=80, default="Gestão Simples")
    valor_mensal = models.DecimalField("valor mensal (R$)", max_digits=10, decimal_places=2)
    dia_vencimento = models.PositiveSmallIntegerField(
        "dia de vencimento", default=10, validators=[MinValueValidator(1), MaxValueValidator(28)],
    )
    inicio = models.DateField("início do serviço")
    primeiro_vencimento = models.DateField(
        "primeiro vencimento", null=True, blank=True,
        help_text="Nada vence antes desta data (o período anterior é da implantação). "
                  "Em branco: o primeiro dia de vencimento a partir do início.",
    )
    dias_tolerancia = models.PositiveSmallIntegerField(
        "dias de tolerância", default=5,
        help_text="Dias depois do vencimento com acesso normal; no dia seguinte a eles, a assinatura é suspensa.",
    )
    gateway = models.CharField(max_length=20, choices=GATEWAYS, default=WOOVI)
    status = models.CharField(max_length=10, choices=STATUS, default=ATIVA)
    motivo_suspensao = models.CharField(max_length=20, choices=MOTIVOS_SUSPENSAO, blank=True)
    suspensa_em = models.DateTimeField(null=True, blank=True)
    criada_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "assinatura"
        verbose_name_plural = "assinaturas"

    def __str__(self):
        return f"{self.academia} — {self.plano}"

    @property
    def suspensa(self):
        return self.status == self.SUSPENSA

    @property
    def suspensa_por_atraso(self):
        return self.status == self.SUSPENSA and self.motivo_suspensao == self.INADIMPLENCIA


class FaturaAssinatura(models.Model):
    """Cada lançamento da assinatura: a mensalidade de um mês (ou a
    implantação, registrada como paga). Guarda a cobrança Pix da Woovi em
    vigor: um Pix vencido é trocado por outro, sempre com o
    ``correlation_id`` começando por ``assinatura-<id da fatura>-``."""

    PENDENTE = "pendente"
    ATRASADA = "atrasada"
    PAGA = "paga"
    CANCELADA = "cancelada"
    STATUS = [(PENDENTE, "Pendente"), (ATRASADA, "Atrasada"), (PAGA, "Paga"), (CANCELADA, "Cancelada")]
    EM_ABERTO = (PENDENTE, ATRASADA)

    MENSALIDADE = "mensalidade"
    IMPLANTACAO = "implantacao"
    TIPOS = [(MENSALIDADE, "Mensalidade"), (IMPLANTACAO, "Implantação")]

    assinatura = models.ForeignKey(Assinatura, on_delete=models.CASCADE, related_name="faturas")
    tipo = models.CharField(max_length=15, choices=TIPOS, default=MENSALIDADE)
    competencia = models.DateField()
    valor = models.DecimalField(max_digits=10, decimal_places=2)
    vencimento = models.DateField()
    status = models.CharField(max_length=10, choices=STATUS, default=PENDENTE)
    pago_em = models.DateTimeField(null=True, blank=True)

    # Cobrança Pix em vigor na Woovi (a interface nunca cita o provedor).
    correlation_id = models.CharField(max_length=100, blank=True, db_index=True)
    woovi_charge_id = models.CharField("id da cobrança na Woovi", max_length=100, blank=True)
    br_code = models.TextField("Pix copia e cola", blank=True)
    pix_expira_em = models.DateTimeField(null=True, blank=True)

    criada_em = models.DateTimeField(auto_now_add=True)
    atualizada_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-vencimento", "-pk"]
        verbose_name = "fatura da assinatura"
        verbose_name_plural = "faturas da assinatura"
        constraints = [
            models.UniqueConstraint(
                fields=["assinatura", "competencia"], condition=Q(tipo="mensalidade"),
                name="fatura_assinatura_unica_por_mes",
            ),
        ]

    def __str__(self):
        return f"{self.assinatura.academia} — {self.referencia}"

    @property
    def referencia(self):
        if self.tipo == self.IMPLANTACAO:
            return "Implantação"
        return f"Mensalidade {self.competencia:%m/%Y}"

    @property
    def em_aberto(self):
        return self.status in self.EM_ABERTO

    def dias_em_atraso(self, hoje=None):
        hoje = hoje or timezone.localdate()
        return max(0, (hoje - self.vencimento).days) if self.em_aberto else 0

    @property
    def fim_da_tolerancia(self):
        """Último dia de acesso normal; a suspensão vem no dia seguinte."""
        return self.vencimento + timedelta(days=self.assinatura.dias_tolerancia)

    @property
    def suspensao_em(self):
        return self.fim_da_tolerancia + timedelta(days=1)

    @property
    def pix_vigente(self):
        return bool(self.br_code) and self.pix_expira_em is not None and self.pix_expira_em > timezone.now()
