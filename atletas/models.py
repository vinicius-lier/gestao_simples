from datetime import timedelta

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator
from django.core.exceptions import ValidationError
from django.utils import timezone

from academias.models import Academia

from .arquivos import apagar_arquivo, caminho_documento, caminho_foto, validar_documento


class Responsavel(models.Model):
    # Portal da família: entra com CPF ou WhatsApp + senha. A senha é criada
    # pela própria família (no 1º acesso pelo link do WhatsApp) e só o hash
    # fica no banco. Muitas senhas erradas travam o login por um tempo.
    TENTATIVAS_ANTES_DO_BLOQUEIO = 5
    BLOQUEIO = timedelta(minutes=15)

    academia = models.ForeignKey(
        Academia,
        on_delete=models.CASCADE,
        related_name="responsaveis",
    )

    nome = models.CharField(max_length=150)
    cpf = models.CharField(max_length=14, blank=True)
    telefone = models.CharField(max_length=20, blank=True)
    whatsapp = models.CharField(max_length=20)
    email = models.EmailField(blank=True)

    senha = models.CharField(max_length=128, blank=True, editable=False)
    senha_definida_em = models.DateTimeField(null=True, blank=True, editable=False)
    tentativas_login = models.PositiveSmallIntegerField(default=0, editable=False)
    bloqueado_ate = models.DateTimeField(null=True, blank=True, editable=False)

    ativo = models.BooleanField(default=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.nome

    @property
    def tem_senha(self):
        return bool(self.senha)

    @property
    def bloqueado(self):
        return self.bloqueado_ate is not None and self.bloqueado_ate > timezone.now()

    def definir_senha(self, senha):
        self.senha = make_password(senha)
        self.senha_definida_em = timezone.now()
        self.tentativas_login = 0
        self.bloqueado_ate = None
        self.save(update_fields=['senha', 'senha_definida_em', 'tentativas_login', 'bloqueado_ate'])

    def conferir_senha(self, senha):
        """Confere a senha e registra a tentativa: acerto zera o contador;
        erros seguidos bloqueiam o login por ``BLOQUEIO``."""
        if not self.senha or self.bloqueado:
            return False
        if check_password(senha, self.senha):
            if self.tentativas_login or self.bloqueado_ate:
                self.tentativas_login = 0
                self.bloqueado_ate = None
                self.save(update_fields=['tentativas_login', 'bloqueado_ate'])
            return True
        self.tentativas_login += 1
        if self.tentativas_login >= self.TENTATIVAS_ANTES_DO_BLOQUEIO:
            self.tentativas_login = 0
            self.bloqueado_ate = timezone.now() + self.BLOQUEIO
        self.save(update_fields=['tentativas_login', 'bloqueado_ate'])
        return False


class Atleta(models.Model):
    proprio_responsavel = models.BooleanField(default=False, verbose_name='o aluno é o responsável financeiro')
    STATUS = [
        ("ativo", "Ativo"),
        ("inativo", "Inativo"),
        ("trancado", "Trancado"),
    ]

    academia = models.ForeignKey(
        Academia,
        on_delete=models.CASCADE,
        related_name="atletas",
    )

    responsavel_financeiro = models.ForeignKey(
        Responsavel,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="atletas",
        
    )

    nome = models.CharField(max_length=150)
    data_nascimento = models.DateField(null=True,blank=True)
    cpf = models.CharField(max_length=14, blank=True)
    # Quando o aluno é o próprio responsável financeiro, este é também o
    # WhatsApp do responsável — o número que recebe as cobranças.
    telefone = models.CharField('telefone / WhatsApp', max_length=20, blank=True)
    faixa = models.CharField(max_length=50, blank=True)

    status = models.CharField(
        max_length=20,
        choices=STATUS,
        default="ativo",
    )

    observacoes = models.TextField(blank=True)
    rg = models.CharField('Número do RG', max_length=30, blank=True)
    endereco = models.TextField('Endereço completo', blank=True)
    federado = models.BooleanField('Federado?', null=True, blank=True, default=None)
    numero_zempo = models.CharField('Número Zempo', max_length=40, blank=True)
    numero_fjerj = models.CharField('Número FJERJ', max_length=40, blank=True)
    bolsa_percentual = models.DecimalField(
        'Bolsa (%)', max_digits=5, decimal_places=2, default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text='0 = sem bolsa; 100 = bolsa integral. Não se acumula com o desconto de irmãos.',
    )
    beneficio_inicio = models.DateField('Início da bolsa / desconto', null=True, blank=True)
    beneficio_fim = models.DateField('Fim da bolsa / desconto', null=True, blank=True)
    desconto_irmaos = models.BooleanField(
        'Desconto para irmãos', default=False,
        help_text='Marque após confirmar o parentesco. Desconto somente no pagamento em dia.',
    )

    def clean(self):
        super().clean()
        if self.beneficio_inicio and self.beneficio_fim and self.beneficio_fim < self.beneficio_inicio:
            raise ValidationError({'beneficio_fim': 'O fim não pode ser anterior ao início.'})

    def beneficio_valido(self, competencia):
        from calendar import monthrange
        fim_mes = competencia.replace(day=monthrange(competencia.year, competencia.month)[1])
        return (not self.beneficio_inicio or self.beneficio_inicio <= fim_mes) and (
            not self.beneficio_fim or self.beneficio_fim >= competencia)
    # Foto de rosto, já reduzida e sem metadados (ver arquivos.preparar_foto).
    # Servida só pela view do painel, nunca por URL pública.
    foto = models.ImageField(upload_to=caminho_foto, blank=True)
    foto_atualizada_em = models.DateTimeField(null=True, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.nome

    def trocar_foto(self, conteudo):
        """Grava a foto nova (um ContentFile de ``preparar_foto``) e apaga a
        anterior do disco."""
        anterior = self.foto.name if self.foto else ''
        self.foto.save('foto.jpg', conteudo, save=False)
        self.foto_atualizada_em = timezone.now()
        self.save(update_fields=['foto', 'foto_atualizada_em'])
        if anterior and anterior != self.foto.name:
            try:
                self.foto.storage.delete(anterior)
            except OSError:
                pass

    def remover_foto(self):
        apagar_arquivo(self.foto)
        self.foto = ''
        self.foto_atualizada_em = timezone.now()
        self.save(update_fields=['foto', 'foto_atualizada_em'])


class DocumentoAtleta(models.Model):
    """Documento anexado ao cadastro do aluno (identidade, atestado etc.),
    enviado pela equipe no painel ou pela família no recadastramento."""

    IDENTIDADE = 'identidade'
    CPF = 'cpf'
    ATESTADO = 'atestado'
    LAUDO = 'laudo'
    RESIDENCIA = 'residencia'
    IMAGEM = 'imagem'
    FEDERACAO = 'federacao'
    OUTRO = 'outro'
    TIPOS = [
        (IDENTIDADE, 'Certidão de nascimento ou RG'),
        (CPF, 'CPF'),
        (ATESTADO, 'Atestado médico'),
        (LAUDO, 'Laudo ou documento médico'),
        (RESIDENCIA, 'Comprovante de residência'),
        (IMAGEM, 'Autorização de uso de imagem'),
        (FEDERACAO, 'Carteira da federação'),
        (OUTRO, 'Outro documento'),
    ]

    academia = models.ForeignKey(Academia, on_delete=models.CASCADE, related_name='documentos_alunos')
    atleta = models.ForeignKey(Atleta, on_delete=models.CASCADE, related_name='documentos')
    tipo = models.CharField(max_length=20, choices=TIPOS)
    descricao = models.CharField('descrição', max_length=150, blank=True)
    arquivo = models.FileField(upload_to=caminho_documento, validators=[validar_documento], max_length=255)
    nome_original = models.CharField(max_length=255, blank=True)
    tamanho = models.PositiveIntegerField(default=0)
    validade = models.DateField(
        'válido até', null=True, blank=True,
        help_text='Para documentos que vencem, como o atestado médico.',
    )
    enviado_pela_familia = models.BooleanField(default=False)
    enviado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['tipo', '-criado_em', '-pk']
        verbose_name = 'documento do aluno'
        verbose_name_plural = 'documentos dos alunos'

    def __str__(self):
        return f'{self.get_tipo_display()} — {self.atleta}'

    @property
    def titulo(self):
        return self.descricao or self.get_tipo_display()

    @property
    def vencido(self):
        return self.validade is not None and self.validade < timezone.localdate()

    @property
    def eh_imagem(self):
        return self.arquivo.name.lower().endswith(('.jpg', '.jpeg', '.png', '.webp'))
