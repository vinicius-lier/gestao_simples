from datetime import timedelta

from django.contrib.auth.hashers import check_password, make_password
from django.db import models
from django.utils import timezone

from academias.models import Academia


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
    criado_em = models.DateTimeField(auto_now_add=True)
    
    

    def __str__(self):
        return self.nome