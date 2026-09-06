from django.db import models
from academias.models import Academia


class Modalidade(models.Model):
    academia = models.ForeignKey(
        Academia,
        on_delete=models.CASCADE,
        related_name="modalidades",
    )

    nome = models.CharField(max_length=150)
    descricao = models.TextField(blank=True)

    ativo = models.BooleanField(default=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'modalidade'
        verbose_name_plural = 'modalidades'

    def __str__(self):
        return self.nome


class Graduacao(models.Model):
    academia = models.ForeignKey(Academia, on_delete=models.CASCADE)
    modalidade = models.ForeignKey(Modalidade, on_delete=models.PROTECT, related_name='graduacoes')
    nome = models.CharField(max_length=100)
    ordem = models.PositiveSmallIntegerField(default=1)
    ativo = models.BooleanField(default=True)

    class Meta:
        ordering = ['modalidade__nome', 'ordem', 'nome']
        verbose_name = 'graduação / faixa'
        verbose_name_plural = 'graduações / faixas'
        constraints = [models.UniqueConstraint(fields=['modalidade', 'nome'], name='graduacao_modalidade_nome')]

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.modalidade_id and self.academia_id and self.modalidade.academia_id != self.academia_id:
            raise ValidationError({'modalidade': 'A modalidade pertence a outra academia.'})

    def __str__(self):
        return f'{self.modalidade.nome} — {self.nome}'


class Professor(models.Model):
    academia = models.ForeignKey(Academia, on_delete=models.CASCADE, related_name='professores')
    nome = models.CharField(max_length=150)
    email = models.EmailField(blank=True)
    telefone = models.CharField(max_length=20, blank=True)
    graduacao = models.CharField(max_length=100, blank=True)
    faixa = models.ForeignKey(Graduacao, on_delete=models.PROTECT, null=True, blank=True, verbose_name='Modalidade e faixa')
    ativo = models.BooleanField(default=True)

    class Meta:
        ordering = ['nome']
        verbose_name_plural = 'professores'

    def __str__(self):
        return self.nome


class Turma(models.Model):
    unidade = models.ForeignKey('academias.Unidade', on_delete=models.PROTECT, null=True, blank=True, related_name='turmas')
    docente = models.ForeignKey(Professor, on_delete=models.PROTECT, null=True, blank=True, related_name='turmas', verbose_name='professor responsável')

    academia = models.ForeignKey(
        Academia,
        on_delete=models.CASCADE,
        related_name="turmas",
    )

    modalidade = models.ForeignKey(
        Modalidade,
        on_delete=models.PROTECT,
        related_name="turmas",
    )

    nome = models.CharField(max_length=150)
    professor = models.CharField(max_length=150, blank=True)
    dias_semana = models.CharField(max_length=100, blank=True)
    horario = models.TimeField(null=True, blank=True)
    local = models.CharField(max_length=150, blank=True)

    valor_mensalidade = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text='Valor de referência da mensalidade desta turma.',
    )
    dia_vencimento = models.PositiveSmallIntegerField(default=10)

    ativo = models.BooleanField(default=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    def clean(self):
        from django.core.exceptions import ValidationError
        erros = {}
        for campo in ('modalidade', 'unidade', 'docente'):
            if getattr(self, campo + '_id') and self.academia_id and getattr(self, campo).academia_id != self.academia_id:
                erros[campo] = 'O cadastro pertence a outra academia.'
        if self.valor_mensalidade is not None and self.valor_mensalidade < 0:
            erros['valor_mensalidade'] = 'O valor não pode ser negativo.'
        if not 1 <= self.dia_vencimento <= 31:
            erros['dia_vencimento'] = 'O dia de vencimento deve estar entre 1 e 31.'
        if erros:
            raise ValidationError(erros)

    def __str__(self):
        return f"{self.modalidade.nome} - {self.nome}"
