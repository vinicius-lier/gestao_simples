from datetime import date

from django.conf import settings
from django.db import models
from django.db.models import Prefetch, Q
from django.utils import timezone

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

    @property
    def cobranca_pix_vigente(self):
        """O Pix desta mensalidade que ainda aceita pagamento, ou None.
        Listas podem pré-carregar as ativas em ``_cobrancas_ativas`` (ver
        ``CobrancaPix.prefetch_ativas``) para não consultar linha a linha."""
        cobrancas = getattr(self, "_cobrancas_ativas", None)
        if cobrancas is None:
            cobrancas = self.cobrancas_pix.filter(status=CobrancaPix.ATIVA)
        return next((c for c in cobrancas if c.vigente), None)

    @property
    def pix_vigente(self):
        return self.cobranca_pix_vigente is not None


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


class ContaRecebimento(models.Model):
    """Chave Pix onde a academia recebe as mensalidades.

    No provedor de pagamento cada chave Pix identifica uma subconta: os
    pagamentos caem nela e são repassados para a chave (ver ``Repasse``). A
    chave de uma conta nunca é editada — trocar a chave cria outra conta e
    desativa a anterior, que fica como histórico, com as cobranças e os
    repasses que já estavam ligados a ela."""

    CPF = "cpf"
    CNPJ = "cnpj"
    EMAIL = "email"
    TELEFONE = "telefone"
    ALEATORIA = "aleatoria"
    TIPOS_CHAVE = [
        (CPF, "CPF"),
        (CNPJ, "CNPJ"),
        (EMAIL, "E-mail"),
        (TELEFONE, "Celular"),
        (ALEATORIA, "Chave aleatória"),
    ]

    academia = models.ForeignKey(
        Academia,
        on_delete=models.PROTECT,
        related_name="contas_recebimento",
    )
    tipo_chave = models.CharField(max_length=10, choices=TIPOS_CHAVE)
    pix_key = models.CharField("chave Pix", max_length=100)
    ativa = models.BooleanField(default=True)
    saque_bloqueado = models.BooleanField(
        default=False,
        help_text="O provedor recusou transferir para esta chave (chave inválida ou restrita).",
    )

    criada_em = models.DateTimeField(auto_now_add=True)
    criada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
    )
    desativada_em = models.DateTimeField(null=True, blank=True)
    desativada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
    )

    class Meta:
        ordering = ["-criada_em", "-pk"]
        verbose_name = "conta de recebimento"
        verbose_name_plural = "contas de recebimento"
        constraints = [
            models.UniqueConstraint(
                fields=["academia"],
                condition=Q(ativa=True),
                name="uma_conta_recebimento_ativa_por_academia",
            )
        ]

    def __str__(self):
        return f"{self.academia} — {self.pix_key}"

    @classmethod
    def ativa_da(cls, academia):
        return cls.objects.filter(academia=academia, ativa=True).first()


class CobrancaPix(models.Model):
    """Um Pix gerado para uma mensalidade. Ao longo do tempo uma mensalidade
    pode ter vários (o anterior expirou, a chave de recebimento mudou), mas
    no máximo um ATIVO. Cada um guarda a conta de recebimento para a qual o
    valor foi direcionado quando foi criado."""

    ATIVA = "ativa"
    PAGA = "paga"
    EXPIRADA = "expirada"
    CANCELADA = "cancelada"
    STATUS = [
        (ATIVA, "Ativa"),
        (PAGA, "Paga"),
        (EXPIRADA, "Expirada"),
        (CANCELADA, "Cancelada"),
    ]

    mensalidade = models.ForeignKey(
        Mensalidade,
        on_delete=models.PROTECT,
        related_name="cobrancas_pix",
    )
    conta_recebimento = models.ForeignKey(
        ContaRecebimento,
        on_delete=models.PROTECT,
        related_name="cobrancas_pix",
    )
    correlation_id = models.CharField(max_length=100, unique=True)
    transaction_id = models.CharField(max_length=100, blank=True)
    valor = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=10, choices=STATUS, default=ATIVA, db_index=True)
    br_code = models.TextField(help_text="Pix copia e cola.")
    link_pagamento = models.URLField(max_length=500, blank=True)
    expira_em = models.DateTimeField()
    pago_em = models.DateTimeField(null=True, blank=True)

    # O Pix é criado sem split (a Woovi não aceita split de 100%): o valor
    # entra na conta principal e o repasse credita o LÍQUIDO na subconta —
    # a taxa da Woovi é paga pela academia.
    taxa = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text="Taxa da Woovi sobre este Pix (vem no aviso de pagamento). "
                  "Se ficar vazia, o repasse para em 'Requer atenção' até ser informada.",
    )
    valor_liquido = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text="Valor pago menos a taxa: o que é creditado na subconta da academia.",
    )
    creditado_em = models.DateTimeField(
        null=True, blank=True, help_text="Quando o líquido foi creditado na subconta.",
    )
    credito_incerto = models.BooleanField(
        default=False,
        help_text="O último pedido de crédito terminou sem resposta: conferir o extrato "
                  "da subconta antes de pedir de novo (o crédito não é idempotente).",
    )

    criada_em = models.DateTimeField(auto_now_add=True)
    atualizada_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-criada_em", "-pk"]
        verbose_name = "cobrança Pix"
        verbose_name_plural = "cobranças Pix"
        constraints = [
            models.UniqueConstraint(
                fields=["mensalidade"],
                condition=Q(status="ativa"),
                name="uma_cobranca_pix_ativa_por_mensalidade",
            )
        ]

    def __str__(self):
        return f"{self.mensalidade} — {self.get_status_display()}"

    @property
    def vigente(self):
        return self.status == self.ATIVA and self.expira_em > timezone.now()

    @classmethod
    def prefetch_ativas(cls):
        """Para listas de mensalidades: carrega as ativas de uma vez só."""
        return Prefetch(
            "cobrancas_pix",
            queryset=cls.objects.filter(status=cls.ATIVA),
            to_attr="_cobrancas_ativas",
        )


class EventoWebhook(models.Model):
    """Aviso recebido do provedor de pagamento, gravado antes de ser
    processado. ``chave`` identifica o evento (tipo + correlationID +
    endToEndId/transação): o mesmo aviso reenviado não é processado de novo.
    O ``payload`` guarda só os campos operacionais — sem nome, CPF ou conta
    de quem pagou."""

    RECEBIDO = "recebido"
    PROCESSADO = "processado"
    IGNORADO = "ignorado"
    ERRO = "erro"
    STATUS = [
        (RECEBIDO, "Recebido"),
        (PROCESSADO, "Processado"),
        (IGNORADO, "Ignorado"),
        (ERRO, "Erro"),
    ]

    chave = models.CharField(max_length=255, unique=True)
    tipo = models.CharField(max_length=80)
    correlation_id = models.CharField(max_length=100, blank=True, db_index=True)
    payload = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=10, choices=STATUS, default=RECEBIDO)
    erro = models.TextField(blank=True)
    recebido_em = models.DateTimeField(auto_now_add=True)
    processado_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-recebido_em", "-pk"]
        verbose_name = "evento de webhook"
        verbose_name_plural = "eventos de webhook"

    def __str__(self):
        return f"{self.tipo} {self.correlation_id} ({self.get_status_display()})"


class Repasse(models.Model):
    """Transferência do dinheiro recebido para a chave Pix da academia.

    Cada pagamento cria (ou reaproveita) um repasse PENDENTE da conta; o job
    ``processar_repasses`` consulta o saldo real e transfere TUDO o que
    houver — nunca assume que o saldo é o valor da mensalidade. O banco
    garante no máximo um repasse aberto (pendente, processando ou falha
    aguardando nova tentativa) por conta: pagamentos quase simultâneos
    viram um único saque, e dois saques da mesma conta nunca correm juntos.

    FALHA = a última tentativa falhou e há outra agendada
    (``proxima_tentativa_em``). REQUER_ATENCAO = esgotou as tentativas ou a
    chave foi recusada; precisa de alguém da plataforma."""

    PENDENTE = "pendente"
    PROCESSANDO = "processando"
    CONCLUIDA = "concluida"
    FALHA = "falha"
    REQUER_ATENCAO = "requer_atencao"
    STATUS = [
        (PENDENTE, "Pendente"),
        (PROCESSANDO, "Processando"),
        (CONCLUIDA, "Concluída"),
        (FALHA, "Falha — nova tentativa agendada"),
        (REQUER_ATENCAO, "Requer atenção"),
    ]
    STATUS_ABERTOS = (PENDENTE, PROCESSANDO, FALHA)

    academia = models.ForeignKey(Academia, on_delete=models.PROTECT, related_name="repasses")
    conta_recebimento = models.ForeignKey(
        ContaRecebimento,
        on_delete=models.PROTECT,
        related_name="repasses",
    )
    pix_key_destino = models.CharField(max_length=100)
    valor = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text="Valor efetivamente enviado (saldo disponível no momento do saque).",
    )
    status = models.CharField(max_length=15, choices=STATUS, default=PENDENTE, db_index=True)
    tentativas = models.PositiveSmallIntegerField(default=0)
    proxima_tentativa_em = models.DateTimeField(default=timezone.now, db_index=True)
    saldo_novo_pendente = models.BooleanField(
        default=False,
        help_text="Chegou pagamento enquanto este saque estava em andamento: "
                  "ao concluir, outro repasse é aberto para o saldo novo.",
    )
    reconciliar = models.BooleanField(
        default=False,
        help_text="O último pedido de saque terminou sem resposta: conferir o "
                  "extrato antes de pedir de novo (o saque não é idempotente).",
    )
    correlation_id = models.CharField(max_length=100, blank=True, db_index=True)
    end_to_end_id = models.CharField(max_length=100, blank=True)
    erro = models.TextField(blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    processado_em = models.DateTimeField(
        null=True, blank=True, help_text="Quando o saque foi pedido ao provedor.",
    )
    concluido_em = models.DateTimeField(null=True, blank=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-criado_em", "-pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["conta_recebimento"],
                condition=Q(status__in=["pendente", "processando", "falha"]),
                name="um_repasse_aberto_por_conta",
            )
        ]

    def __str__(self):
        return f"Repasse {self.pk} — {self.get_status_display()}"
