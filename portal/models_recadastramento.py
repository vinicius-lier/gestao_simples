import secrets

from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone


class CampanhaRecadastramento(models.Model):
    """Um recadastramento: a escola pede às famílias dos alunos já
    cadastrados que confiram e atualizem os dados (ficha, questionário de
    saúde, termos), e mandem foto e documentos. Aberto pela professora
    quando quiser ou, uma vez por ano, pela rotina diária no mês escolhido
    (``Academia.recadastramento_mes``). Cada aluno recebe um pedido com um
    link só dele."""

    academia = models.ForeignKey('academias.Academia', on_delete=models.CASCADE, related_name='recadastramentos')
    titulo = models.CharField('título', max_length=100)
    prazo = models.DateField('responder até')
    mensagem = models.TextField(
        'recado para as famílias', blank=True,
        help_text='Vai no início da página do recadastramento. Opcional.',
    )
    pedir_foto = models.BooleanField('pedir foto do aluno', default=True)
    documentos_pedidos = models.CharField(
        max_length=200, blank=True,
        help_text='Tipos de documento pedidos (DocumentoAtleta.TIPOS), separados por vírgula.',
    )
    automatica = models.BooleanField(default=False, help_text='Aberta pela rotina anual.')
    criada_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    criada_em = models.DateTimeField(auto_now_add=True)
    encerrada_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-criada_em', '-pk']
        verbose_name = 'recadastramento'
        verbose_name_plural = 'recadastramentos'

    def __str__(self):
        return self.titulo

    @property
    def aberta(self):
        return self.encerrada_em is None

    def tipos_documentos(self):
        """[(tipo, rótulo)] dos documentos pedidos, na ordem de TIPOS."""
        from atletas.models import DocumentoAtleta

        pedidos = set(filter(None, self.documentos_pedidos.split(',')))
        return [(tipo, rotulo) for tipo, rotulo in DocumentoAtleta.TIPOS if tipo in pedidos]

    def contagem(self):
        # order_by() vazio: a ordenação padrão (nome do aluno) entraria no GROUP BY.
        por_status = {
            linha['status']: linha['n']
            for linha in self.pedidos.order_by().values('status').annotate(n=models.Count('pk'))
        }
        total = sum(por_status.values())
        return {
            'total': total,
            'respondidos': por_status.get(PedidoRecadastramento.RESPONDIDO, 0),
            'enviados': por_status.get(PedidoRecadastramento.ENVIADO, 0),
            'aguardando_envio': por_status.get(PedidoRecadastramento.PENDENTE, 0)
                                + por_status.get(PedidoRecadastramento.ENVIANDO, 0),
            'sem_contato': por_status.get(PedidoRecadastramento.SEM_CONTATO, 0),
            'percentual': round(100 * por_status.get(PedidoRecadastramento.RESPONDIDO, 0) / total) if total else 0,
        }


class PedidoRecadastramento(models.Model):
    """O pedido de recadastramento de um aluno, com o link da família."""

    PENDENTE = 'pendente'          # ainda não enviado (ou falhou e vai tentar de novo)
    ENVIANDO = 'enviando'          # um envio está em andamento
    ENVIADO = 'enviado'            # mensagem saiu; aguardando a família
    SEM_CONTATO = 'sem_contato'    # sem WhatsApp cadastrado: mandar o link à mão
    RESPONDIDO = 'respondido'
    STATUS = [
        (PENDENTE, 'Aguardando envio'),
        (ENVIANDO, 'Enviando'),
        (ENVIADO, 'Enviado, aguardando a família'),
        (SEM_CONTATO, 'Sem WhatsApp cadastrado'),
        (RESPONDIDO, 'Respondido'),
    ]
    # Depois de tantas falhas seguidas, a rotina diária para de tentar
    # sozinha; o botão "Enviar" do painel tenta de novo.
    MAXIMO_TENTATIVAS = 3

    campanha = models.ForeignKey(CampanhaRecadastramento, on_delete=models.CASCADE, related_name='pedidos')
    academia = models.ForeignKey('academias.Academia', on_delete=models.CASCADE, related_name='+')
    atleta = models.ForeignKey('atletas.Atleta', on_delete=models.CASCADE, related_name='recadastramentos')
    token = models.CharField(max_length=64, unique=True, editable=False)
    status = models.CharField(max_length=12, choices=STATUS, default=PENDENTE)
    telefone = models.CharField('WhatsApp de envio', max_length=20, blank=True)
    lembrete = models.BooleanField(default=False, help_text='O próximo envio é um lembrete.')
    tentativas_envio = models.PositiveSmallIntegerField(default=0)
    ultimo_erro = models.CharField(max_length=255, blank=True)
    enviado_em = models.DateTimeField(null=True, blank=True)
    envio_iniciado_em = models.DateTimeField(null=True, blank=True)
    respondido_em = models.DateTimeField(null=True, blank=True)
    ficha = models.OneToOneField(
        'portal.FichaMatricula', on_delete=models.SET_NULL, null=True, blank=True, related_name='recadastramento',
    )
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['atleta__nome', 'pk']
        verbose_name = 'pedido de recadastramento'
        verbose_name_plural = 'pedidos de recadastramento'
        constraints = [
            models.UniqueConstraint(fields=['campanha', 'atleta'], name='recadastramento_um_por_aluno'),
        ]

    def __str__(self):
        return f'{self.atleta} — {self.campanha}'

    def save(self, *args, **kwargs):
        if not self.token:
            self.token = secrets.token_urlsafe(32)
        super().save(*args, **kwargs)

    def url(self):
        return reverse('portal:recadastramento_publico', args=[self.token])

    @property
    def aberto_para_resposta(self):
        return self.campanha.aberta and self.status != self.RESPONDIDO and self.academia.ativo

    @property
    def prazo_vencido(self):
        return self.campanha.prazo < timezone.localdate()
