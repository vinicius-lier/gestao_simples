from datetime import timedelta

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models
from django.utils import timezone


class ConfiguracaoExperimental(models.Model):
    """Padrões da academia para as aulas experimentais.

    ``vagas_padrao`` / ``antecedencia_horas_padrao`` / ``fila_habilitada_padrao``
    apenas pré-preenchem o formulário de uma nova aula — cada AulaExperimental
    guarda o próprio valor e pode sobrescrever. Já ``ativo`` e ``janela_dias``
    são globais: valem para toda a agenda no momento em que o site é lido.
    """
    academia = models.OneToOneField(
        'academias.Academia', on_delete=models.CASCADE, related_name='config_experimental'
    )
    ativo = models.BooleanField('Aceitar agendamentos pelo site', default=True)
    vagas_padrao = models.PositiveSmallIntegerField(
        'Vagas por aula (padrão)', default=1, validators=[MinValueValidator(1)]
    )
    antecedencia_horas_padrao = models.PositiveSmallIntegerField(
        'Antecedência mínima para agendar (horas, padrão)', default=2
    )
    janela_dias = models.PositiveSmallIntegerField(
        'Liberar agendamento com no máximo (dias de antecedência)',
        default=30,
        validators=[MinValueValidator(1)],
    )
    fila_habilitada_padrao = models.BooleanField(
        'Habilitar fila de espera por padrão', default=True
    )

    class Meta:
        verbose_name = 'configuração de aulas experimentais'
        verbose_name_plural = 'configuração de aulas experimentais'

    def __str__(self):
        return f'Aulas experimentais — {self.academia}'

    @classmethod
    def resolver(cls, academia):
        """Config da academia; se não existir, uma instância com os padrões
        (NÃO salva — usada nas leituras públicas, sem efeito colateral)."""
        return cls.objects.filter(academia=academia).first() or cls(academia=academia)

    def limite_janela(self):
        return timezone.now() + timedelta(days=self.janela_dias)


class AulaExperimental(models.Model):
    # modalidades usa o app_label histórico 'servicos' (ver ModalidadesConfig.label).
    turma = models.ForeignKey('servicos.Turma', on_delete=models.PROTECT)
    inicio = models.DateTimeField('Início do período')
    fim = models.DateTimeField('Fim do período')
    vagas = models.PositiveSmallIntegerField(default=1, validators=[MinValueValidator(1)])
    inscricoes_abrem = models.DateTimeField('Abertura das inscrições', default=timezone.now)
    antecedencia_horas = models.PositiveSmallIntegerField('Encerrar inscrições quantas horas antes do fim', default=2)
    fila_habilitada = models.BooleanField('Habilitar fila de espera', default=True)
    ativa = models.BooleanField(default=True)

    class Meta:
        ordering = ['inicio', 'pk']
        constraints = [
            models.CheckConstraint(condition=models.Q(vagas__gte=1), name='experimental_vagas_positivas'),
            models.CheckConstraint(condition=models.Q(fim__gt=models.F('inicio')), name='experimental_periodo_valido'),
        ]

    @property
    def inscricoes_encerram(self):
        return self.fim - timedelta(hours=self.antecedencia_horas or 0)

    def clean(self):
        erros = {}
        if self.inicio and self.fim and self.fim <= self.inicio:
            erros['fim'] = 'O fim do período deve ser depois do início.'
        if self.inicio and self.fim and self.inscricoes_abrem and self.inscricoes_abrem >= self.inscricoes_encerram:
            erros['inscricoes_abrem'] = 'A abertura deve ocorrer antes do encerramento das inscrições.'
        if self.turma_id:
            t = self.turma
            if not t.unidade_id or t.unidade.academia_id != t.academia_id or t.modalidade.academia_id != t.academia_id:
                erros['turma'] = 'Selecione uma turma com unidade e modalidade da mesma academia.'
        if self.pk and self.vagas < self.ocupadas:
            erros['vagas'] = 'O limite não pode ser menor que o total de vagas ocupadas.'
        if erros:
            raise ValidationError(erros)

    @property
    def ocupadas(self):
        return self.inscricoes.exclude(status__in=['espera', 'cancelado']).count()

    @property
    def vagas_disponiveis(self):
        return max(0, self.vagas - self.ocupadas)

    @property
    def tem_fila(self):
        return self.inscricoes.filter(status='espera').exists()

    @property
    def aberta(self):
        t = self.turma
        return (self.ativa and t.ativo and t.academia.ativo and t.unidade_id
                and t.unidade.ativo and t.modalidade.ativo
                and t.unidade.academia_id == t.academia_id
                and t.modalidade.academia_id == t.academia_id
                and self.inscricoes_abrem <= timezone.now() < self.inscricoes_encerram)

    def disponivel_no_site(self, config=None):
        """``aberta`` + as regras globais da academia (site ligado e dentro da
        janela de antecedência)."""
        config = config or ConfiguracaoExperimental.resolver(self.turma.academia)
        return self.aberta and config.ativo and self.inicio <= config.limite_janela()

    def __str__(self):
        ini = timezone.localtime(self.inicio)
        fim = timezone.localtime(self.fim)
        if ini.date() == fim.date():
            periodo = f'{ini:%d/%m/%Y %H:%M}–{fim:%H:%M}'
        else:
            periodo = f'{ini:%d/%m/%Y %H:%M} a {fim:%d/%m/%Y %H:%M}'
        return f'{self.turma} — {periodo}'


class InscricaoExperimental(models.Model):
    STATUS = [('confirmado', 'Confirmado'), ('espera', 'Fila de espera'), ('cancelado', 'Cancelado'),
              ('presente', 'Presente'), ('faltou', 'Faltou'), ('convertido', 'Convertido em matrícula')]
    aula = models.ForeignKey(AulaExperimental, on_delete=models.PROTECT, related_name='inscricoes')
    nome = models.CharField('Nome do participante', max_length=150)
    idade = models.PositiveSmallIntegerField(validators=[MaxValueValidator(120)])
    responsavel = models.CharField('Responsável (obrigatório para menores de 18 anos)', max_length=150, blank=True)
    telefone = models.CharField('WhatsApp com DDD', max_length=20)
    email = models.EmailField('E-mail', blank=True)
    status = models.CharField(max_length=12, choices=STATUS, default='confirmado')
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['criado_em', 'pk']
        constraints = [models.UniqueConstraint(fields=['aula', 'nome', 'telefone'], condition=~models.Q(status='cancelado'), name='experimental_inscricao_unica')]

    def __str__(self):
        return f'{self.nome} — {self.aula}'
