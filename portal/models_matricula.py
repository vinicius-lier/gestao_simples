import secrets
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone


class ConviteMatricula(models.Model):
    """Link que o professor gera e envia para a família preencher a matrícula.

    O professor escolhe unidade/modalidade/turma na geração; a família só
    informa os dados do atleta e do responsável. Ao enviar, cria-se
    Atleta + Responsável + Matrícula **inativa** (``ativo=False``) — nada
    entra no fluxo financeiro até um professor/admin ativar no painel.
    Uso único: um link vale por uma matrícula.
    """

    PENDENTE = 'pendente'        # gerado, aguardando a família preencher
    PREENCHIDO = 'preenchido'    # enviado, aguardando revisão no painel
    ATIVADO = 'ativado'          # matrícula ativada no painel
    CANCELADO = 'cancelado'
    STATUS = [
        (PENDENTE, 'Aguardando preenchimento'),
        (PREENCHIDO, 'Preenchido — revisar'),
        (ATIVADO, 'Matrícula ativada'),
        (CANCELADO, 'Cancelado'),
    ]

    academia = models.ForeignKey('academias.Academia', on_delete=models.CASCADE, related_name='convites_matricula')
    unidade = models.ForeignKey('academias.Unidade', on_delete=models.PROTECT, null=True, blank=True, related_name='convites_matricula')
    modalidade = models.ForeignKey('servicos.Modalidade', on_delete=models.PROTECT, related_name='convites_matricula')
    turma = models.ForeignKey('servicos.Turma', on_delete=models.PROTECT, null=True, blank=True, related_name='convites_matricula')

    # Valor/vencimento aplicados na matrícula; em branco herdam da turma.
    valor_mensalidade = models.DecimalField('Valor da mensalidade (R$)', max_digits=10, decimal_places=2, null=True, blank=True)
    dia_vencimento = models.PositiveSmallIntegerField('Dia de vencimento', null=True, blank=True)

    convidado_nome = models.CharField('Nome do aluno (referência)', max_length=150, blank=True)
    convidado_whatsapp = models.CharField('WhatsApp da família (opcional)', max_length=20, blank=True)
    observacao_interna = models.CharField('Observação interna', max_length=255, blank=True)

    criado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    token = models.CharField(max_length=64, unique=True, editable=False)
    status = models.CharField(max_length=12, choices=STATUS, default=PENDENTE)
    criado_em = models.DateTimeField(auto_now_add=True)
    expira_em = models.DateTimeField()

    atleta = models.ForeignKey('atletas.Atleta', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    matricula = models.ForeignKey('matriculas.Matricula', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    preenchido_em = models.DateTimeField(null=True, blank=True)
    ativado_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-criado_em', '-pk']
        verbose_name = 'convite de matrícula'
        verbose_name_plural = 'convites de matrícula'

    def __str__(self):
        alvo = self.convidado_nome or (self.atleta.nome if self.atleta_id else 'sem nome')
        return f'{alvo} — {self.turma or self.modalidade}'

    @classmethod
    def gerar(cls, *, academia, modalidade, turma=None, unidade=None, criado_por=None,
              convidado_nome='', convidado_whatsapp='', observacao_interna='',
              valor_mensalidade=None, dia_vencimento=None, validade_dias=7):
        return cls.objects.create(
            academia=academia,
            unidade=unidade,
            modalidade=modalidade,
            turma=turma,
            criado_por=criado_por,
            convidado_nome=convidado_nome.strip(),
            convidado_whatsapp=convidado_whatsapp.strip(),
            observacao_interna=observacao_interna.strip(),
            valor_mensalidade=valor_mensalidade,
            dia_vencimento=dia_vencimento,
            token=secrets.token_urlsafe(32),
            expira_em=timezone.now() + timedelta(days=validade_dias),
        )

    @property
    def expirado(self):
        return self.expira_em <= timezone.now()

    @property
    def aberto_para_preenchimento(self):
        return self.status == self.PENDENTE and not self.expirado

    def url(self):
        return reverse('portal:matricula_convite', args=[self.token])

    def valor_efetivo(self):
        if self.valor_mensalidade is not None:
            return self.valor_mensalidade
        return self.turma.valor_mensalidade if self.turma_id else None

    def vencimento_efetivo(self):
        if self.dia_vencimento:
            return self.dia_vencimento
        return self.turma.dia_vencimento if self.turma_id else 10
