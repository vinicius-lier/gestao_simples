from django.db import models

# Create your models here.
class Academia(models.Model):
    nome = models.CharField(max_length=150)
    nome_fantasia = models.CharField(max_length=150, blank=True)
    cnpj = models.CharField(max_length=18, unique=True)
    telefone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)

    ativo = models.BooleanField(default=True)
    criado = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.nome

class Unidade(models.Model):
    academia = models.ForeignKey(Academia, on_delete=models.CASCADE, related_name='unidades')
    nome = models.CharField(max_length=150)
    endereco = models.CharField(max_length=250, blank=True)
    telefone = models.CharField(max_length=20, blank=True)
    ativo = models.BooleanField(default=True)

    class Meta:
        ordering = ['nome']
        constraints = [models.UniqueConstraint(fields=['academia', 'nome'], name='unidade_nome_academia')]

    def __str__(self):
        return self.nome


class IntegracaoWhatsApp(models.Model):
    """Configuração *não sensível* da integração de WhatsApp de uma academia.

    Guarda apenas o que pode ficar em texto claro no banco: qual provedor
    usar, URLs, nome da instância, estado da conexão. As credenciais (tokens
    de API, chave do Evolution, token do n8n) continuam em variáveis de
    ambiente / settings — aqui fica só, opcionalmente, o *nome* da variável
    de ambiente que as guarda (`credencial_ref`), nunca o valor.
    """

    PROVIDER_META = 'meta'
    PROVIDER_EVOLUTION = 'evolution'
    PROVIDERS = [
        (PROVIDER_META, 'Meta Cloud API'),
        (PROVIDER_EVOLUTION, 'Evolution API'),
    ]

    STATUS_DESCONECTADO = 'desconectado'
    STATUS_AGUARDANDO_QRCODE = 'aguardando_qrcode'
    STATUS_CONECTADO = 'conectado'
    STATUS_ERRO = 'erro'
    STATUS_CONEXAO = [
        (STATUS_DESCONECTADO, 'Desconectado'),
        (STATUS_AGUARDANDO_QRCODE, 'Aguardando QR Code'),
        (STATUS_CONECTADO, 'Conectado'),
        (STATUS_ERRO, 'Erro'),
    ]

    academia = models.OneToOneField(
        Academia,
        on_delete=models.CASCADE,
        related_name='integracao_whatsapp',
    )

    provider = models.CharField(
        max_length=20,
        choices=PROVIDERS,
        default=PROVIDER_META,
    )

    numero_whatsapp = models.CharField(max_length=20, blank=True, default='')
    numero_avisos = models.CharField(
        'WhatsApp para avisos da escola',
        max_length=20,
        blank=True,
        default='',
        help_text='Recebe os avisos internos, como matrícula nova para conferir e ativar.',
    )
    evolution_base_url = models.URLField(max_length=300, blank=True, default='')
    evolution_instance_name = models.CharField(max_length=100, blank=True, default='')
    n8n_webhook_url = models.URLField(max_length=300, blank=True, default='')

    credencial_ref = models.CharField(
        max_length=100,
        blank=True,
        default='',
        help_text=(
            'Nome da variável de ambiente que guarda a credencial deste '
            'provedor. O valor da credencial NÃO é armazenado no banco.'
        ),
    )

    status_conexao = models.CharField(
        max_length=20,
        choices=STATUS_CONEXAO,
        default=STATUS_DESCONECTADO,
    )
    ultimo_status = models.CharField(max_length=255, blank=True, default='')
    ultimo_status_em = models.DateTimeField(null=True, blank=True)
    ultima_conexao_em = models.DateTimeField(null=True, blank=True)

    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'integração de WhatsApp'
        verbose_name_plural = 'integrações de WhatsApp'

    def __str__(self):
        return f'{self.academia} — {self.get_provider_display()}'
