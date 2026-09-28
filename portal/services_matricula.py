"""Efetivação e revisão dos convites de matrícula.

O envio público cria Atleta + Responsável + Matrícula **inativa** e avisa a
escola pelo WhatsApp. Na ativação (no painel, só administrador) quem ativa
confere o dia de vencimento e define o 1º vencimento. Mensalidades só são
geradas para matrículas ativas, então nenhuma cobrança Pix é criada antes
da ativação.
"""

import logging

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from academias.models import Academia
from atletas.models import Atleta, Responsavel
from matriculas.models import Matricula
from .forms import digits
from .models import ConviteMatricula, FichaMatricula

logger = logging.getLogger(__name__)

def _achar_ou_criar_responsavel(academia, *, nome, cpf, whatsapp, email):
    cpf_d = digits(cpf or '')
    wa_d = digits(whatsapp or '')
    nome = (nome or '').strip()
    matches = [
        r for r in Responsavel.objects.filter(academia=academia)
        if (cpf_d and digits(r.cpf) == cpf_d)
        or (not cpf_d and r.nome.strip().casefold() == nome.casefold() and digits(r.whatsapp) == wa_d)
    ]
    if len(matches) > 1:
        raise ValidationError('Há responsáveis duplicados nesta academia. Ajuste pelo painel antes de continuar.')
    if matches:
        return matches[0]
    return Responsavel.objects.create(
        academia=academia, nome=nome, cpf=cpf_d, whatsapp=wa_d, email=(email or '').strip(),
    )


def registrar_ficha(convite, atleta, dados):
    """Guarda as respostas da ficha do convite, com o texto dos termos e da
    declaração que a família aceitou."""
    from .models_matricula import PERGUNTAS_SAUDE

    academia = convite.academia
    saude = {}
    for campo, _pergunta, campo_detalhe, _detalhe in PERGUNTAS_SAUDE:
        saude[campo] = dados.get(campo)
        if campo_detalhe:
            saude[campo_detalhe] = (dados.get(campo_detalhe) or '').strip()
    nascimento = dados.get('data_nascimento')
    return FichaMatricula.objects.create(
        academia=academia,
        atleta=atleta,
        convite=convite,
        origem=FichaMatricula.CONVITE,
        respondida_em=timezone.now(),
        nome_aluno=dados['nome'],
        data_nascimento_informada=f'{nascimento:%d/%m/%Y}' if nascimento else '',
        responsavel_nome=dados.get('responsavel_nome', ''),
        telefone_contato=dados.get('telefone', ''),
        email_contato=dados.get('email', ''),
        autorizados_buscar=dados.get('autorizados_buscar', '').strip(),
        vencimento_preferido=dados.get('vencimento_preferido'),
        aceitou_termos=bool(dados.get('aceite_termos')),
        termos_aceitos=academia.termos_matricula.strip() if dados.get('aceite_termos') else '',
        aceitou_declaracao=bool(dados.get('aceite_declaracao')),
        declaracao_aceita=academia.declaracao_matricula.strip() if dados.get('aceite_declaracao') else '',
        **saude,
    )


@transaction.atomic
def efetivar_convite(convite, dados):
    """Cria os registros a partir dos dados do formulário público."""
    Academia.objects.select_for_update().get(pk=convite.academia_id)
    convite.refresh_from_db()
    if not convite.aberto_para_preenchimento:
        raise ValidationError('Este link não está mais disponível.')

    responsavel = _achar_ou_criar_responsavel(
        convite.academia, nome=dados['responsavel_nome'], cpf='',
        whatsapp=dados['telefone'], email=dados.get('email', ''),
    )

    atleta = Atleta.objects.create(
        academia=convite.academia,
        nome=dados['nome'],
        data_nascimento=dados.get('data_nascimento'),
        status='ativo',
        responsavel_financeiro=responsavel,
    )

    valor = convite.valor_efetivo()
    if valor is None:
        raise ValidationError('A turma não tem valor de mensalidade definido. Fale com a escola.')

    matricula = Matricula(
        academia=convite.academia,
        atleta=atleta,
        unidade=convite.unidade,
        modalidade=convite.modalidade,
        turma=convite.turma,
        valor_mensalidade=valor,
        valor_apos_vencimento=convite.valor_apos_vencimento_efetivo(),
        taxa_matricula=convite.taxa_matricula_efetiva(),
        # O dia fixado pela escola no convite vale; senão, o que a família
        # escolheu na ficha. Quem ativa confere e pode mudar.
        dia_vencimento=convite.dia_vencimento or dados.get('vencimento_preferido') or convite.vencimento_efetivo(),
        data_inicio=timezone.localdate(),
        ativo=False,
    )
    matricula.save()  # Matricula.save() roda full_clean()

    registrar_ficha(convite, atleta, dados)

    convite.atleta = atleta
    convite.matricula = matricula
    convite.status = ConviteMatricula.PREENCHIDO
    convite.preenchido_em = timezone.now()
    convite.save(update_fields=['atleta', 'matricula', 'status', 'preenchido_em'])
    return convite


def cobrar_taxa_se_primeira(matricula, primeira):
    """Gera a taxa de matrícula quando a matrícula acabou de ser ativada
    pela primeira vez (``primeira`` é calculado antes de a cobrança começar)
    e, depois do commit, manda a cobrança pelo WhatsApp. Aluno que não está
    ativo não é cobrado agora (igual às mensalidades)."""
    from financeiro.services import gerar_taxa_matricula

    if not primeira or matricula.atleta.status != 'ativo':
        return None
    taxa = gerar_taxa_matricula(matricula)
    if taxa is not None:
        transaction.on_commit(lambda: _enviar_taxa(taxa))
    return taxa


def _enviar_taxa(taxa):
    from financeiro.lembretes import enviar_cobranca_agora

    try:
        enviar_cobranca_agora(taxa)
    except Exception:  # melhor esforço: a taxa já está na lista de cobranças
        logger.exception('Cobrança da taxa de matrícula %s não enviada.', taxa.pk)


@transaction.atomic
def ativar_matricula(matricula, *, dia_vencimento=None, primeiro_vencimento=None):
    """Liga a matrícula e começa a cobrança no 1º vencimento definido por
    quem ativou (sem ele, o próximo dia de vencimento a partir de hoje). Na
    primeira ativação, gera também a taxa de matrícula, com vencimento no
    dia."""
    from financeiro.services import iniciar_cobranca, primeira_ativacao

    primeira = primeira_ativacao(matricula)
    if dia_vencimento:
        matricula.dia_vencimento = dia_vencimento
    matricula.ativo = True
    matricula.save()
    iniciar_cobranca(matricula, primeiro_vencimento)
    cobrar_taxa_se_primeira(matricula, primeira)
    return matricula


@transaction.atomic
def ativar_convite(convite, *, dia_vencimento=None, primeiro_vencimento=None):
    convite = ConviteMatricula.objects.select_for_update().select_related('matricula').get(pk=convite.pk)
    if convite.status != ConviteMatricula.PREENCHIDO or convite.matricula_id is None:
        raise ValidationError('Só é possível ativar um convite já preenchido pela família.')

    # A cobrança começa na ativação, não no preenchimento pela família.
    ativar_matricula(
        convite.matricula, dia_vencimento=dia_vencimento, primeiro_vencimento=primeiro_vencimento,
    )
    convite.status = ConviteMatricula.ATIVADO
    convite.ativado_em = timezone.now()
    convite.save(update_fields=['status', 'ativado_em'])
    return convite


@transaction.atomic
def cancelar_convite(convite):
    convite = ConviteMatricula.objects.select_for_update().get(pk=convite.pk)
    if convite.status == ConviteMatricula.ATIVADO:
        raise ValidationError('Não é possível cancelar um convite já ativado — desative a matrícula pelo cadastro do aluno.')
    convite.status = ConviteMatricula.CANCELADO
    convite.save(update_fields=['status'])
    return convite
