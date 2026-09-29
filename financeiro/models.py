from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Prefetch, Q
from django.utils import timezone

from academias.models import Academia
from matriculas.models import Matricula


class MensalidadeQuerySet(models.QuerySet):
    def em_aberto(self):
        return self.filter(status__in=("pendente", "vencida"))

    def atrasadas(self, hoje=None):
        hoje = hoje or timezone.localdate()
        return self.filter(status__in=("pendente", "vencida"), vencimento__lt=hoje)

    def marcar_vencidas(self, hoje=None):
        """Atualiza para 'vencida' as pendentes cujo vencimento já passou.

        Não mexe em pagas/canceladas/isentas. Idempotente e segura para
        chamar a cada carregamento de tela (sem depender de um job externo).
        """
        hoje = hoje or timezone.localdate()
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

    MENSALIDADE = "mensalidade"
    TAXA_MATRICULA = "taxa_matricula"
    TIPOS = [
        (MENSALIDADE, "Mensalidade"),
        (TAXA_MATRICULA, "Taxa de matrícula"),
    ]

    # A taxa de matrícula usa a mesma estrutura (Pix, lembretes, baixa):
    # uma por matrícula, com a competência do mês em que foi gerada.
    tipo = models.CharField(max_length=20, choices=TIPOS, default=MENSALIDADE)

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

    # Cobrado a partir do dia seguinte ao vencimento; vazio: o valor não muda.
    valor_apos_vencimento = models.DecimalField(
        "valor após o vencimento",
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
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

    # O que foi efetivamente pago (em dia ou com o valor após o vencimento).
    # Vazio em pagamentos antigos: vale ``valor``.
    valor_pago = models.DecimalField(
        max_digits=10,
        decimal_places=2,
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
                condition=Q(tipo="mensalidade"),
                name="mensalidade_unica_por_competencia",
            ),
            models.UniqueConstraint(
                fields=["matricula"],
                condition=Q(tipo="taxa_matricula"),
                name="uma_taxa_matricula_por_matricula",
            ),
        ]

    def __str__(self):
        return f"{self.matricula.atleta.nome} - {self.referencia}"

    @property
    def eh_taxa_matricula(self):
        return self.tipo == self.TAXA_MATRICULA

    @property
    def referencia(self):
        """Como a cobrança aparece nas listas e mensagens: o mês da
        mensalidade ou "Taxa de matrícula"."""
        if self.eh_taxa_matricula:
            return "Taxa de matrícula"
        return f"{self.competencia:%m/%Y}"

    @property
    def descricao(self):
        """"Mensalidade 10/2026" ou "Taxa de matrícula" (Pix e mensagens)."""
        if self.eh_taxa_matricula:
            return "Taxa de matrícula"
        return f"Mensalidade {self.competencia:%m/%Y}"

    @property
    def esta_atrasada(self):
        return self.status in ("pendente", "vencida") and self.vencimento < timezone.localdate()

    def valor_devido(self, hoje=None):
        """O valor a pagar em ``hoje``: até o dia do vencimento, ``valor``;
        depois, ``valor_apos_vencimento`` (quando definido)."""
        hoje = hoje or timezone.localdate()
        if self.valor_apos_vencimento is not None and hoje > self.vencimento:
            return self.valor_apos_vencimento
        return self.valor

    @property
    def valor_atual(self):
        """Para as telas: o que foi pago, se já está paga; senão, o valor
        devido hoje."""
        if self.status == "paga":
            return self.valor_pago if self.valor_pago is not None else self.valor
        return self.valor_devido()

    @property
    def muda_apos_vencimento(self):
        """Ainda em dia, mas com valor maior depois do vencimento."""
        return (
            self.valor_apos_vencimento is not None
            and self.valor_apos_vencimento != self.valor
            and self.status in ("pendente", "vencida")
            and timezone.localdate() <= self.vencimento
        )

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
    """Origem financeira: subconta legada ou conta própria autenticada.

    A identidade de uma conta com histórico é imutável. Desativar uma
    conta para novas cobranças preserva seus Pix e repasses anteriores.
    Credenciais são referências a segredos mantidos somente no servidor.
    """

    LEGADO_SUBCONTA = "legado_subconta"
    CONTA_PROPRIA = "conta_propria"
    MODELOS = [(LEGADO_SUBCONTA, "Subconta legada"), (CONTA_PROPRIA, "Conta própria")]
    NAO_CONFIGURADA = "nao_configurada"
    CONFIGURANDO = "configurando"
    AGUARDANDO = "aguardando"
    CONECTADA = "conectada"
    ERRO = "erro"
    STATUS = [
        (NAO_CONFIGURADA, "Não configurada"), (CONFIGURANDO, "Configuração em andamento"),
        (AGUARDANDO, "Aguardando validação"), (CONECTADA, "Conectada"), (ERRO, "Erro"),
    ]

    # Defaults legados preservam registros existentes. Contas novas do portal
    # são criadas explicitamente como próprias, inativas até a validação.
    modelo_recebimento = models.CharField(max_length=20, choices=MODELOS, default=LEGADO_SUBCONTA)
    provider = models.CharField(max_length=20, default="woovi", editable=False)
    provider_account_id = models.CharField(max_length=100, blank=True)
    credencial_ref = models.CharField(max_length=100, default="WOOVI_APP_ID", editable=False)
    api_base_url = models.URLField(blank=True, editable=False)
    credencial_fingerprint = models.CharField(max_length=64, blank=True, editable=False)
    status = models.CharField(max_length=20, choices=STATUS, default=NAO_CONFIGURADA)
    onboarding_status = models.CharField(max_length=40, blank=True)
    onboarding_correlation_id = models.CharField(max_length=100, blank=True, editable=False)
    onboarding_url = models.URLField(max_length=1000, blank=True, editable=False)
    conectada_em = models.DateTimeField(null=True, blank=True)
    taxa_ciente_em = models.DateTimeField(null=True, blank=True)

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
    tipo_chave = models.CharField(max_length=10, choices=TIPOS_CHAVE, blank=True)
    pix_key = models.CharField("chave Pix", max_length=100, blank=True)
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
            ),
            models.UniqueConstraint(
                fields=["academia"], condition=Q(modelo_recebimento="conta_propria"),
                name="uma_conta_propria_por_academia",
            ),
        ]

    def __str__(self):
        destino = self.pix_key if self.legada else "Conta Woovi da academia"
        return f"{self.academia} — {destino}"

    @property
    def legada(self):
        return self.modelo_recebimento == self.LEGADO_SUBCONTA

    def save(self, *args, **kwargs):
        if self.pk:
            anterior = type(self).objects.get(pk=self.pk)
            if anterior.conectada_em or anterior.cobrancas_pix.exists() or anterior.repasses.exists():
                campos = ("academia_id", "modelo_recebimento", "provider", "provider_account_id",
                          "credencial_ref", "api_base_url", "credencial_fingerprint", "pix_key")
                if any(getattr(self, campo) != getattr(anterior, campo) for campo in campos):
                    raise ValidationError("A origem de uma conta com histórico não pode ser alterada.")
        return super().save(*args, **kwargs)

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
    modelo_recebimento = models.CharField(
        max_length=20, choices=ContaRecebimento.MODELOS, default=ContaRecebimento.LEGADO_SUBCONTA,
        editable=False,
    )
    correlation_id = models.CharField(max_length=100, unique=True)
    transaction_id = models.CharField(max_length=100, blank=True)
    valor = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=10, choices=STATUS, default=ATIVA, db_index=True)
    br_code = models.TextField(help_text="Pix copia e cola.")
    link_pagamento = models.URLField(max_length=500, blank=True)
    expira_em = models.DateTimeField()
    pago_em = models.DateTimeField(null=True, blank=True)
    valor_pago = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    # Taxa real retornada pela API, nunca derivada da informação comercial.
    # Crédito em subconta e repasse aplicam-se apenas ao legado.
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

    def save(self, *args, **kwargs):
        if self._state.adding:
            self.modelo_recebimento = self.conta_recebimento.modelo_recebimento
            if self.mensalidade.academia_id != self.conta_recebimento.academia_id:
                raise ValidationError("Cobrança e conta devem pertencer à mesma academia.")
        else:
            anterior = type(self).objects.only("conta_recebimento_id", "modelo_recebimento", "mensalidade_id").get(pk=self.pk)
            if any(getattr(self, c) != getattr(anterior, c) for c in (
                "conta_recebimento_id", "modelo_recebimento", "mensalidade_id",
            )):
                raise ValidationError("A origem da cobrança não pode ser alterada.")
        return super().save(*args, **kwargs)

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
