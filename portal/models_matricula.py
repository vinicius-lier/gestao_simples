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

    # Valores/vencimento aplicados na matrícula; em branco herdam da turma.
    valor_mensalidade = models.DecimalField('Valor da mensalidade (R$)', max_digits=10, decimal_places=2, null=True, blank=True)
    valor_apos_vencimento = models.DecimalField('Valor após o vencimento (R$)', max_digits=10, decimal_places=2, null=True, blank=True)
    taxa_matricula = models.DecimalField('Taxa de matrícula (R$)', max_digits=10, decimal_places=2, null=True, blank=True)
    dia_vencimento = models.PositiveSmallIntegerField('Dia de vencimento', null=True, blank=True)

    # Link permanente do site: várias famílias usam o mesmo link, que não
    # expira. Cada envio vira um convite próprio (``link_origem``), com as
    # mesmas condições, e segue a revisão normal no painel.
    permanente = models.BooleanField(
        'link permanente do site', default=False,
        help_text='O mesmo link serve para várias famílias e aparece no site da escola (/matricula/).',
    )
    link_origem = models.ForeignKey(
        'self', on_delete=models.SET_NULL, null=True, blank=True, related_name='respostas',
        verbose_name='link permanente de origem',
    )

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
              valor_mensalidade=None, valor_apos_vencimento=None, taxa_matricula=None,
              dia_vencimento=None, validade_dias=7, permanente=False):
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
            valor_apos_vencimento=valor_apos_vencimento,
            taxa_matricula=taxa_matricula,
            dia_vencimento=dia_vencimento,
            token=secrets.token_urlsafe(32),
            permanente=permanente,
            # O permanente não expira (a data só preenche o campo obrigatório).
            expira_em=timezone.now() + timedelta(days=36500 if permanente else validade_dias),
        )

    @property
    def expirado(self):
        return self.expira_em <= timezone.now()

    @property
    def aberto_para_preenchimento(self):
        if self.permanente:
            return self.status == self.PENDENTE and self.academia.ativo
        return self.status == self.PENDENTE and not self.expirado

    @classmethod
    def link_do_site(cls):
        """O link permanente em uso no site (o mais recente ainda aberto)."""
        return cls.objects.filter(
            permanente=True, status=cls.PENDENTE, academia__ativo=True,
        ).select_related('academia', 'modalidade', 'turma', 'turma__unidade', 'unidade').order_by('-criado_em', '-pk').first()

    def nova_resposta(self):
        """Um convite só para este envio do link permanente, com as mesmas
        condições; é ele que é preenchido, revisado e ativado."""
        return ConviteMatricula.objects.create(
            academia=self.academia, unidade=self.unidade, modalidade=self.modalidade, turma=self.turma,
            valor_mensalidade=self.valor_mensalidade, valor_apos_vencimento=self.valor_apos_vencimento,
            taxa_matricula=self.taxa_matricula, dia_vencimento=self.dia_vencimento,
            criado_por=self.criado_por, observacao_interna=self.observacao_interna,
            link_origem=self, token=secrets.token_urlsafe(32), expira_em=timezone.now() + timedelta(days=1),
        )

    def url(self):
        return reverse('portal:matricula_convite', args=[self.token])

    def _da_turma(self, campo):
        if getattr(self, campo) is not None:
            return getattr(self, campo)
        return getattr(self.turma, campo) if self.turma_id else None

    def valor_efetivo(self):
        return self._da_turma('valor_mensalidade')

    def valor_apos_vencimento_efetivo(self):
        return self._da_turma('valor_apos_vencimento')

    def taxa_matricula_efetiva(self):
        return self._da_turma('taxa_matricula')

    def vencimento_efetivo(self):
        if self.dia_vencimento:
            return self.dia_vencimento
        return self.turma.dia_vencimento if self.turma_id else 10


# Perguntas da ficha de matrícula, com o texto do formulário que a escola já
# usava (Google Forms). Cada item: (campo, pergunta, campo do detalhe,
# pergunta do detalhe) — o detalhe é pedido quando a resposta é "Sim".
PERGUNTAS_SAUDE = (
    ('saude_coracao',
     'Algum médico já disse que a criança possui algum problema de coração ou pressão arterial, '
     'e que somente deveria realizar atividade física supervisionado por profissionais de saúde?',
     None, None),
    ('saude_dor_peito',
     'A criança já se queixou de dores no peito quando pratica atividade física?',
     None, None),
    ('saude_dor_peito_ultimo_mes',
     'No último mês, a criança reclamou de sentir dores no peito ao praticar atividade física?',
     None, None),
    ('saude_tontura',
     'A criança apresenta algum desequilíbrio devido à tontura e/ou perda momentânea da consciência?',
     None, None),
    ('saude_osso_articulacao',
     'A criança possui algum problema ósseo ou articular, que pode ser afetado ou agravado pela atividade física?',
     None, None),
    ('saude_medicacao',
     'A criança toma atualmente algum tipo de medicação de uso contínuo?',
     'saude_medicacao_qual',
     'Se você respondeu SIM na pergunta anterior, escreva o nome do remédio:'),
    ('saude_tratamento_pressao',
     'A criança realiza algum tipo de tratamento médico para pressão arterial ou problemas cardíacos?',
     None, None),
    ('saude_tratamento_continuo',
     'A criança realiza algum tratamento médico contínuo, que possa ser afetado ou prejudicado com a atividade física?',
     'saude_tratamento_qual',
     'Se você respondeu SIM na pergunta anterior, escreva o nome do tratamento:'),
    ('saude_cirurgia',
     'A criança já foi submetida a algum tipo de cirurgia, que comprometa de alguma forma a atividade física?',
     'saude_cirurgia_qual',
     'Se você respondeu SIM na pergunta anterior, escreva qual foi a cirurgia:'),
    ('saude_outra_razao',
     'Sabe de alguma outra razão pela qual a atividade física possa eventualmente comprometer a saúde da criança?',
     'saude_outra_razao_qual',
     'Se você respondeu SIM na pergunta anterior, descreva o motivo:'),
)

VENCIMENTOS_FICHA = (
    (5, 'Até o dia 05 de cada mês'),
    (10, 'Até o dia 10 de cada mês'),
    (15, 'Até o dia 15 de cada mês'),
)

ACEITE_TERMOS = 'Estou ciente e concordo em prosseguir com a matrícula.'
ACEITE_DECLARACAO = 'Estou ciente e concordo.'


class FichaMatricula(models.Model):
    """Respostas da ficha de matrícula de um aluno, como a família
    respondeu: pelo convite de matrícula ou importadas da planilha do
    formulário antigo. Os dados do cadastro do aluno ficam no Atleta; aqui
    fica o registro do que foi informado e aceito (inclusive o texto dos
    termos no momento do aceite)."""

    CONVITE = 'convite'
    PLANILHA = 'planilha'
    ORIGENS = [
        (CONVITE, 'Convite de matrícula'),
        (PLANILHA, 'Planilha do formulário'),
    ]

    academia = models.ForeignKey('academias.Academia', on_delete=models.CASCADE, related_name='fichas_matricula')
    atleta = models.ForeignKey('atletas.Atleta', on_delete=models.CASCADE, related_name='fichas_matricula')
    convite = models.OneToOneField(
        ConviteMatricula, on_delete=models.SET_NULL, null=True, blank=True, related_name='ficha',
    )
    origem = models.CharField(max_length=10, choices=ORIGENS)
    respondida_em = models.DateTimeField()

    nome_aluno = models.CharField('nome do aluno', max_length=150)
    # Como foi respondido (a planilha tem datas digitadas de vários jeitos).
    data_nascimento_informada = models.CharField('data de nascimento', max_length=30, blank=True)
    responsavel_nome = models.CharField('nome do responsável', max_length=150, blank=True)
    responsavel_cpf = models.CharField('CPF do responsável', max_length=14, blank=True)
    telefone_contato = models.CharField('telefone para contato', max_length=150, blank=True)
    email_contato = models.CharField('e-mail para contato', max_length=254, blank=True)
    autorizados_buscar = models.TextField('quem está autorizado a buscar', blank=True)
    vencimento_preferido = models.PositiveSmallIntegerField(
        'melhor data para o vencimento', choices=VENCIMENTOS_FICHA, null=True, blank=True,
    )

    saude_coracao = models.BooleanField(null=True, blank=True)
    saude_dor_peito = models.BooleanField(null=True, blank=True)
    saude_dor_peito_ultimo_mes = models.BooleanField(null=True, blank=True)
    saude_tontura = models.BooleanField(null=True, blank=True)
    saude_osso_articulacao = models.BooleanField(null=True, blank=True)
    saude_medicacao = models.BooleanField(null=True, blank=True)
    saude_medicacao_qual = models.TextField(blank=True)
    saude_tratamento_pressao = models.BooleanField(null=True, blank=True)
    saude_tratamento_continuo = models.BooleanField(null=True, blank=True)
    saude_tratamento_qual = models.TextField(blank=True)
    saude_cirurgia = models.BooleanField(null=True, blank=True)
    saude_cirurgia_qual = models.TextField(blank=True)
    saude_outra_razao = models.BooleanField(null=True, blank=True)
    saude_outra_razao_qual = models.TextField(blank=True)

    aceitou_termos = models.BooleanField(default=False)
    termos_aceitos = models.TextField(blank=True)
    aceitou_declaracao = models.BooleanField(default=False)
    declaracao_aceita = models.TextField(blank=True)

    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-respondida_em', '-pk']
        verbose_name = 'ficha de matrícula'
        verbose_name_plural = 'fichas de matrícula'
        constraints = [
            # A mesma resposta importada duas vezes não duplica.
            models.UniqueConstraint(fields=['atleta', 'respondida_em'], name='ficha_unica_por_resposta'),
        ]

    def __str__(self):
        return f'{self.nome_aluno} — {self.respondida_em:%d/%m/%Y}'

    def respostas_saude(self):
        """Lista das perguntas de saúde com a resposta e o detalhe, na
        ordem do formulário."""
        return [
            {
                'pergunta': pergunta,
                'resposta': getattr(self, campo),
                'detalhe_pergunta': pergunta_detalhe,
                'detalhe': getattr(self, campo_detalhe) if campo_detalhe else '',
            }
            for campo, pergunta, campo_detalhe, pergunta_detalhe in PERGUNTAS_SAUDE
        ]

    @property
    def alertas_saude(self):
        return [r for r in self.respostas_saude() if r['resposta']]
