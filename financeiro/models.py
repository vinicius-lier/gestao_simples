from datetime import date

from django.db import models

from academias.models import Academia
from matriculas.models import Matricula


class MensalidadeQuerySet(models.QuerySet):
    def em_aberto(self):
        return self.filter(status__in=("pendente", "vencida"))

    def atrasadas(self, hoje=None):
        hoje = hoje or date.today()
        return self.filter(status__in=("pendente", "vencida"), vencimento__lt=hoje)

    def marcar_vencidas(self, hoje=None):
        """Atualiza para 'vencida' as pendentes cujo vencimento já passou.

        Não mexe em pagas/canceladas/isentas. Idempotente e segura para
        chamar a cada carregamento de tela (sem depender de um job externo).
        """
        hoje = hoje or date.today()
        return self.filter(status="pendente", vencimento__lt=hoje).update(status="vencida")


class Mensalidade(models.Model):
    STATUS = [
        ("pendente", "Pendente"),
        ("paga", "Paga"),
        ("vencida", "Vencida"),
        ("cancelada", "Cancelada"),
        ("isenta", "Isenta"),
    ]

    FORMAS_PAGAMENTO = [
        ("pix", "Pix"),
        ("boleto", "Boleto"),
        ("cartao", "Cartão"),
        ("dinheiro", "Dinheiro"),
        ("outro", "Outro"),
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

    forma_pagamento = models.CharField(
        max_length=20,
        choices=FORMAS_PAGAMENTO,
        blank=True,
    )

    asaas_payment_id = models.CharField(
        max_length=100,
        blank=True,
    )

    asaas_invoice_url = models.URLField(
        max_length=300,
        blank=True,
        help_text='Página de pagamento hospedada pelo Asaas (Pix, boleto ou cartão).',
    )

    asaas_bank_slip_url = models.URLField(
        max_length=300,
        blank=True,
        help_text='PDF do boleto, quando a cobrança aceita esse meio.',
    )

    criado_em = models.DateTimeField(auto_now_add=True)

    objects = MensalidadeQuerySet.as_manager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["matricula", "competencia"],
                name="mensalidade_unica_por_competencia",
            )
        ]

    def __str__(self):
        return f"{self.matricula.atleta.nome} - {self.competencia:%m/%Y}"

    @property
    def esta_atrasada(self):
        return self.status in ("pendente", "vencida") and self.vencimento < date.today()


class LembreteCobranca(models.Model):
    """Registro operacional de cada estágio do lembrete automático de
    cobrança de uma mensalidade.

    O `UniqueConstraint(mensalidade, estagio)` garante que cada estágio
    existe no máximo uma vez por mensalidade (nada de spam). O registro é
    criado ANTES da tentativa de envio e passa por `pendente` -> `enviado`
    ou `pendente`/`erro` -> `erro`. Só `status=enviado` bloqueia novas
    tentativas; `status=erro` pode ser retentado na próxima execução."""

    CINCO_DIAS = "5_dias"
    UM_DIA = "1_dia"
    VENCIMENTO = "vencimento"
    ATRASADA = "atrasada"

    ESTAGIOS = [
        (CINCO_DIAS, "5 dias antes do vencimento"),
        (UM_DIA, "1 dia antes do vencimento"),
        (VENCIMENTO, "No dia do vencimento"),
        (ATRASADA, "Mensalidade atrasada"),
    ]

    PENDENTE = "pendente"
    ENVIADO = "enviado"
    ERRO = "erro"

    STATUS = [
        (PENDENTE, "Pendente"),
        (ENVIADO, "Enviado"),
        (ERRO, "Erro"),
    ]

    mensalidade = models.ForeignKey(
        Mensalidade,
        on_delete=models.CASCADE,
        related_name="lembretes",
    )

    estagio = models.CharField(
        max_length=20,
        choices=ESTAGIOS,
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS,
        default=PENDENTE,
    )

    tentativas = models.PositiveIntegerField(default=0)

    ultimo_erro = models.TextField(blank=True, default="")

    provider = models.CharField(max_length=30, blank=True, default="")

    provider_message_id = models.CharField(max_length=200, blank=True, default="")

    enviado_em = models.DateTimeField(null=True, blank=True)

    atualizado_em = models.DateTimeField(auto_now=True, null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["mensalidade", "estagio"],
                name="lembrete_unico_por_estagio",
            )
        ]

    def __str__(self):
        return f"{self.mensalidade} - {self.get_estagio_display()}"

    @property
    def concluido(self):
        """Estágio já entregue — não deve ser reenviado automaticamente."""
        return self.status == self.ENVIADO