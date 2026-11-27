"""Recadastramento dos alunos já cadastrados.

Fluxo: a escola abre uma campanha (ou a rotina diária abre a anual) →
cada aluno ganha um pedido com link próprio → as mensagens saem pelo
WhatsApp, uma por família (irmãos no mesmo número recebem uma mensagem só,
com um link por aluno) → a família confere a ficha já preenchida, corrige o
que mudou, aceita os termos de novo e manda foto e documentos → o cadastro
do aluno e do responsável é atualizado e a resposta vira uma ficha nova no
histórico ("Recadastramento").
"""
import logging
import threading
import time
from collections import OrderedDict
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.utils import timezone

from atletas.models import Atleta, DocumentoAtleta
from .forms import digits
from .models import CampanhaRecadastramento, FichaMatricula, PedidoRecadastramento

logger = logging.getLogger(__name__)

# Um "Enviando" mais velho que isto ficou para trás (o site reiniciou no
# meio do envio): volta para a fila.
ENVIO_TRAVADO_APOS = timedelta(minutes=30)
PRAZO_PADRAO_DIAS = 30
DOCUMENTOS_PADRAO = (DocumentoAtleta.ATESTADO,)


def telefone_do_aluno(atleta):
    responsavel = atleta.responsavel_financeiro
    if responsavel is not None and responsavel.academia_id == atleta.academia_id and digits(responsavel.whatsapp):
        return digits(responsavel.whatsapp)
    return digits(atleta.telefone)


# ----------------------------------------------------------------- abertura

def alunos_do_publico(academia, turma=None):
    """Alunos ativos da academia (ou só os com matrícula ativa na turma)."""
    alunos = Atleta.objects.filter(academia=academia, status='ativo')
    if turma is not None:
        alunos = alunos.filter(matriculas__turma=turma, matriculas__ativo=True)
    return alunos.select_related('responsavel_financeiro').distinct().order_by('nome')


@transaction.atomic
def abrir_campanha(academia, *, titulo, prazo, alunos, pedir_foto=True, documentos=(),
                   mensagem='', criada_por=None, automatica=False):
    campanha = CampanhaRecadastramento.objects.create(
        academia=academia, titulo=titulo.strip(), prazo=prazo, mensagem=(mensagem or '').strip(),
        pedir_foto=pedir_foto, documentos_pedidos=','.join(documentos),
        criada_por=criada_por, automatica=automatica,
    )
    for atleta in alunos:
        telefone = telefone_do_aluno(atleta)
        PedidoRecadastramento.objects.create(
            campanha=campanha, academia=academia, atleta=atleta, telefone=telefone,
            status=PedidoRecadastramento.PENDENTE if telefone else PedidoRecadastramento.SEM_CONTATO,
        )
    return campanha


def encerrar_campanha(campanha):
    if campanha.encerrada_em is None:
        campanha.encerrada_em = timezone.now()
        campanha.save(update_fields=['encerrada_em'])
    return campanha


def lembrar_quem_nao_respondeu(campanha):
    """Põe de volta na fila, como lembrete, quem recebeu e não respondeu."""
    return campanha.pedidos.filter(status=PedidoRecadastramento.ENVIADO).update(
        status=PedidoRecadastramento.PENDENTE, lembrete=True, tentativas_envio=0, ultimo_erro='',
    )


def reenviar_pedido(pedido):
    """Volta um pedido para a fila de envio (com o WhatsApp atual do
    cadastro). Respondido não é reenviado."""
    if pedido.status == PedidoRecadastramento.RESPONDIDO:
        raise ValidationError('Este aluno já respondeu o recadastramento.')
    pedido.telefone = telefone_do_aluno(pedido.atleta)
    if not pedido.telefone:
        pedido.status = PedidoRecadastramento.SEM_CONTATO
        pedido.save(update_fields=['telefone', 'status'])
        raise ValidationError('O aluno não tem WhatsApp cadastrado (nem o responsável). Copie o link e envie à mão.')
    pedido.status = PedidoRecadastramento.PENDENTE
    pedido.lembrete = pedido.enviado_em is not None
    pedido.tentativas_envio = 0
    pedido.ultimo_erro = ''
    pedido.save(update_fields=['telefone', 'status', 'lembrete', 'tentativas_envio', 'ultimo_erro'])
    return pedido


# ------------------------------------------------------------------- envio

def _link_absoluto(pedido):
    return settings.SITE_URL.rstrip('/') + pedido.url()


def montar_mensagem(campanha, pedidos):
    academia = campanha.academia
    escola = academia.nome_fantasia or academia.nome
    responsavel = pedidos[0].atleta.responsavel_financeiro
    nome = responsavel.nome.split()[0] if responsavel and responsavel.nome.strip() else ''
    saudacao = f'Olá, {nome}!' if nome else 'Olá!'
    um = len(pedidos) == 1
    alvo = pedidos[0].atleta.nome if um else 'dos alunos abaixo'
    if any(p.lembrete for p in pedidos):
        abertura = f'{saudacao} Lembrete da {escola}: ainda falta atualizar o cadastro {"de " + alvo if um else alvo} ({campanha.titulo}).'
    else:
        abertura = f'{saudacao} A {escola} está atualizando o cadastro dos alunos ({campanha.titulo}).'
    extra = ', e envie a foto e os documentos pedidos' if campanha.pedir_foto or campanha.documentos_pedidos else ''
    prazo = f'{campanha.prazo:%d/%m/%Y}'
    if um:
        return (
            f'{abertura} Confira os dados de {alvo}, responda o questionário de saúde{extra} '
            f'até {prazo}, neste link: {_link_absoluto(pedidos[0])}'
        )
    linhas = '\n'.join(f'• {p.atleta.nome}: {_link_absoluto(p)}' for p in pedidos)
    return (
        f'{abertura} Confira os dados, responda o questionário de saúde{extra} até {prazo}. '
        f'Um link para cada aluno:\n{linhas}'
    )


def _liberar_travados(campanha):
    PedidoRecadastramento.objects.filter(
        campanha=campanha, status=PedidoRecadastramento.ENVIANDO,
        envio_iniciado_em__lt=timezone.now() - ENVIO_TRAVADO_APOS,
    ).update(status=PedidoRecadastramento.PENDENTE)


def enviar_pendentes(campanha, *, pausa=None, respeitar_tentativas=False):
    """Manda as mensagens que estão na fila desta campanha, uma por número
    de WhatsApp. Cada grupo é "reservado" (Pendente → Enviando) antes de
    enviar, então dois envios ao mesmo tempo não duplicam mensagem.
    Devolve {'enviados': n de mensagens, 'falhas': n}."""
    from integracoes.whatsapp import enviar_recadastramento
    from integracoes.whatsapp.base import WhatsAppProviderError

    pausa = settings.RECADASTRAMENTO_PAUSA_ENVIO if pausa is None else pausa
    resultado = {'enviados': 0, 'falhas': 0}
    campanha.refresh_from_db()
    if not campanha.aberta:
        return resultado
    _liberar_travados(campanha)
    fila = campanha.pedidos.filter(status=PedidoRecadastramento.PENDENTE).select_related(
        'atleta', 'atleta__responsavel_financeiro', 'campanha__academia',
    )
    if respeitar_tentativas:
        fila = fila.filter(tentativas_envio__lt=PedidoRecadastramento.MAXIMO_TENTATIVAS)
    grupos = OrderedDict()
    for pedido in fila:
        grupos.setdefault(pedido.telefone, []).append(pedido)

    primeiro = True
    for telefone, pedidos in grupos.items():
        ids = [p.pk for p in pedidos]
        reservados = PedidoRecadastramento.objects.filter(
            pk__in=ids, status=PedidoRecadastramento.PENDENTE,
        ).update(status=PedidoRecadastramento.ENVIANDO, envio_iniciado_em=timezone.now())
        if not reservados:
            continue
        pedidos = list(PedidoRecadastramento.objects.filter(
            pk__in=ids, status=PedidoRecadastramento.ENVIANDO,
        ).select_related('atleta', 'atleta__responsavel_financeiro', 'campanha__academia'))
        if not pedidos:
            continue
        if not primeiro and pausa:
            time.sleep(pausa)
        primeiro = False
        try:
            enviar_recadastramento(campanha.academia, telefone, montar_mensagem(campanha, pedidos))
        except (ValueError, WhatsAppProviderError) as erro:
            resultado['falhas'] += 1
            for p in pedidos:
                p.status = PedidoRecadastramento.PENDENTE
                p.tentativas_envio += 1
                p.ultimo_erro = str(erro)[:255]
                p.save(update_fields=['status', 'tentativas_envio', 'ultimo_erro'])
            logger.warning('Recadastramento %s: envio para %s falhou: %s', campanha.pk, telefone[-4:], erro)
        except Exception:
            # Erro inesperado: devolve à fila e segue para a próxima família.
            resultado['falhas'] += 1
            PedidoRecadastramento.objects.filter(pk__in=[p.pk for p in pedidos]).update(
                status=PedidoRecadastramento.PENDENTE, ultimo_erro='Erro inesperado no envio.',
            )
            logger.exception('Recadastramento %s: erro inesperado no envio.', campanha.pk)
        else:
            resultado['enviados'] += 1
            agora = timezone.now()
            PedidoRecadastramento.objects.filter(pk__in=[p.pk for p in pedidos]).update(
                status=PedidoRecadastramento.ENVIADO, enviado_em=agora, lembrete=False,
                tentativas_envio=0, ultimo_erro='',
            )
    return resultado


def disparar_envio(campanha):
    """Começa o envio da fila. Em produção roda em segundo plano (com a
    pausa entre as mensagens); devolve True nesse caso. Sem o segundo
    plano, envia na hora e devolve o resultado."""
    if not getattr(settings, 'RECADASTRAMENTO_EM_SEGUNDO_PLANO', False):
        return enviar_pendentes(campanha, pausa=0)
    threading.Thread(
        target=_enviar_em_segundo_plano, args=(campanha.pk,), name=f'recadastramento-{campanha.pk}', daemon=True,
    ).start()
    return True


def _enviar_em_segundo_plano(campanha_pk):
    try:
        campanha = CampanhaRecadastramento.objects.select_related('academia').get(pk=campanha_pk)
        enviar_pendentes(campanha)
    except Exception:
        logger.exception('Envio do recadastramento %s falhou.', campanha_pk)
    finally:
        connection.close()


# ---------------------------------------------------------- rotina diária

def rotina_diaria(hoje=None):
    """Abre o recadastramento anual no mês escolhido pela academia e
    retoma a fila das campanhas abertas (o que falhou ou ficou para trás).
    Devolve {'abertas': n, 'enviados': n}."""
    from academias.models import Academia

    hoje = hoje or timezone.localdate()
    abertas = 0
    for academia in Academia.objects.filter(ativo=True, recadastramento_mes=hoje.month):
        if _abrir_anual(academia, hoje):
            abertas += 1
    enviados = 0
    for campanha in CampanhaRecadastramento.objects.filter(encerrada_em__isnull=True).select_related('academia'):
        enviados += enviar_pendentes(campanha, respeitar_tentativas=True)['enviados']
    return {'abertas': abertas, 'enviados': enviados}


def _abrir_anual(academia, hoje):
    """Uma por ano, e não se a professora já abriu uma nos últimos 90 dias."""
    if academia.recadastramentos.filter(automatica=True, criada_em__year=hoje.year).exists():
        return None
    if academia.recadastramentos.filter(criada_em__date__gte=hoje - timedelta(days=90)).exists():
        return None
    anterior = academia.recadastramentos.first()
    documentos = (
        [t for t, _ in anterior.tipos_documentos()] if anterior is not None else list(DOCUMENTOS_PADRAO)
    )
    return abrir_campanha(
        academia, titulo=f'Recadastramento {hoje.year}', prazo=hoje + timedelta(days=PRAZO_PADRAO_DIAS),
        alunos=alunos_do_publico(academia), pedir_foto=anterior.pedir_foto if anterior else True,
        documentos=documentos, mensagem=anterior.mensagem if anterior else '', automatica=True,
    )


# --------------------------------------------------------- resposta da família

def dados_iniciais(pedido):
    """O que a família já informou: cadastro atual + última ficha."""
    atleta = pedido.atleta
    responsavel = atleta.responsavel_financeiro
    ficha = atleta.fichas_matricula.first()
    inicial = {
        'nome': atleta.nome,
        'rg': atleta.rg,
        'endereco': atleta.endereco,
        'proprio_responsavel': atleta.proprio_responsavel,
        'cpf': atleta.cpf,
        'data_nascimento': atleta.data_nascimento,
        'federado': 'sim' if atleta.federado is True else 'nao' if atleta.federado is False else '',
        'numero_zempo': atleta.numero_zempo,
        'numero_fjerj': atleta.numero_fjerj,
        'responsavel_nome': responsavel.nome if responsavel else '',
        'responsavel_cpf': (responsavel.cpf if responsavel else '') or (ficha.responsavel_cpf if ficha else ''),
        'telefone': (responsavel.whatsapp if responsavel else '') or atleta.telefone,
        'email': (responsavel.email if responsavel else '') or (ficha.email_contato if ficha else ''),
        'autorizados_buscar': ficha.autorizados_buscar if ficha else '',
    }
    if ficha is not None:
        from .models_matricula import PERGUNTAS_SAUDE

        for campo, _pergunta, campo_detalhe, _detalhe in PERGUNTAS_SAUDE:
            valor = getattr(ficha, campo)
            if valor is not None:
                inicial[campo] = 'sim' if valor else 'nao'
            if campo_detalhe:
                inicial[campo_detalhe] = getattr(ficha, campo_detalhe)
    return inicial


@transaction.atomic
def responder_pedido(pedido, dados, *, foto=None, documentos=()):
    """Grava a resposta da família. ``foto`` é o ContentFile já preparado;
    ``documentos`` é uma lista de (tipo, arquivo enviado)."""
    from .services_matricula import _achar_ou_criar_responsavel, criar_ficha

    pedido = PedidoRecadastramento.objects.select_for_update().select_related(
        'campanha', 'academia', 'atleta', 'atleta__responsavel_financeiro',
    ).get(pk=pedido.pk)
    if not pedido.aberto_para_resposta:
        raise ValidationError('Este recadastramento não está mais aberto.')
    atleta = pedido.atleta
    academia = pedido.academia

    atleta.nome = dados['nome']
    atleta.data_nascimento = dados.get('data_nascimento')
    campos_atleta = ['nome', 'data_nascimento']
    for campo in ('rg', 'endereco', 'federado', 'numero_zempo', 'numero_fjerj'):
        if campo in dados:
            setattr(atleta, campo, dados[campo])
            campos_atleta.append(campo)

    responsavel = atleta.responsavel_financeiro
    proprio = bool(dados.get('proprio_responsavel', atleta.proprio_responsavel))
    if proprio != atleta.proprio_responsavel:
        responsavel = None
    atleta.proprio_responsavel = proprio
    campos_atleta.append('proprio_responsavel')
    if 'cpf' in dados:
        atleta.cpf = digits(dados.get('cpf', atleta.cpf))
        campos_atleta.append('cpf')
    if responsavel is not None and responsavel.academia_id != academia.pk:
        responsavel = None
    if responsavel is None:
        responsavel = _achar_ou_criar_responsavel(
            academia, nome=dados['responsavel_nome'], cpf=dados.get('responsavel_cpf', ''),
            whatsapp=dados['telefone'], email=dados.get('email', ''),
        )
        if proprio and responsavel.nome.strip().casefold() != dados['nome'].strip().casefold():
            raise ValidationError('Este CPF está vinculado a outro responsável. Fale com a escola para conferir o cadastro.')
        atleta.responsavel_financeiro = responsavel
        campos_atleta.append('responsavel_financeiro')
    responsavel.nome = dados['responsavel_nome']
    responsavel.cpf = digits(dados.get('responsavel_cpf', ''))
    responsavel.whatsapp = digits(dados['telefone'])
    responsavel.email = (dados.get('email') or '').strip()
    responsavel.save(update_fields=['nome', 'cpf', 'whatsapp', 'email'])
    if atleta.proprio_responsavel:
        atleta.telefone = digits(dados['telefone'])
        campos_atleta.append('telefone')
    atleta.save(update_fields=campos_atleta)

    # O dia preferido de vencimento não é pedido de novo: fica o da ficha anterior.
    anterior = atleta.fichas_matricula.exclude(vencimento_preferido=None).first()
    ficha = criar_ficha(
        academia, atleta,
        {**dados, 'vencimento_preferido': anterior.vencimento_preferido if anterior else None},
        origem=FichaMatricula.RECADASTRAMENTO,
    )

    if foto is not None:
        atleta.trocar_foto(foto)
    for tipo, arquivo in documentos:
        DocumentoAtleta.objects.create(
            academia=academia, atleta=atleta, tipo=tipo, arquivo=arquivo,
            nome_original=arquivo.name[:255], tamanho=arquivo.size, enviado_pela_familia=True,
        )

    pedido.status = PedidoRecadastramento.RESPONDIDO
    pedido.respondido_em = timezone.now()
    pedido.ficha = ficha
    pedido.save(update_fields=['status', 'respondido_em', 'ficha'])
    return pedido
