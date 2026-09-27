"""Efetivação e revisão dos convites de matrícula.

O envio público cria Atleta + Responsável + Matrícula **inativa** e avisa a
escola pelo WhatsApp. Na ativação (no painel, só administrador) quem ativa
confere o dia de vencimento e define o 1º vencimento. Mensalidades só são
geradas para matrículas ativas, então nenhuma cobrança Pix é criada antes
da ativação.
"""

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from academias.models import Academia
from atletas.models import Atleta, Responsavel
from matriculas.models import Matricula
from .forms import digits
from .models import ConviteMatricula


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


@transaction.atomic
def efetivar_convite(convite, dados):
    """Cria os registros a partir dos dados do formulário público."""
    Academia.objects.select_for_update().get(pk=convite.academia_id)
    convite.refresh_from_db()
    if not convite.aberto_para_preenchimento:
        raise ValidationError('Este link não está mais disponível.')

    proprio = bool(dados.get('proprio_responsavel'))
    if proprio:
        nome_r, cpf_r = dados['nome'], dados.get('cpf', '')
    else:
        nome_r, cpf_r = dados.get('responsavel_nome', ''), dados.get('responsavel_cpf', '')
    responsavel = _achar_ou_criar_responsavel(
        convite.academia, nome=nome_r, cpf=cpf_r,
        whatsapp=dados['responsavel_whatsapp'], email=dados.get('responsavel_email', ''),
    )

    atleta = Atleta.objects.create(
        academia=convite.academia,
        nome=dados['nome'],
        data_nascimento=dados.get('data_nascimento'),
        cpf=digits(dados.get('cpf', '')),
        telefone=digits(dados['responsavel_whatsapp']) if proprio else '',
        faixa=dados.get('faixa', ''),
        observacoes=dados.get('observacoes', ''),
        status='ativo',
        proprio_responsavel=proprio,
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
        dia_vencimento=convite.vencimento_efetivo(),
        data_inicio=timezone.localdate(),
        ativo=False,
    )
    matricula.save()  # Matricula.save() roda full_clean()

    convite.atleta = atleta
    convite.matricula = matricula
    convite.status = ConviteMatricula.PREENCHIDO
    convite.preenchido_em = timezone.now()
    convite.save(update_fields=['atleta', 'matricula', 'status', 'preenchido_em'])
    return convite


@transaction.atomic
def ativar_matricula(matricula, *, dia_vencimento=None, primeiro_vencimento=None):
    """Liga a matrícula e começa a cobrança no 1º vencimento definido por
    quem ativou (sem ele, o próximo dia de vencimento a partir de hoje)."""
    from financeiro.services import iniciar_cobranca

    if dia_vencimento:
        matricula.dia_vencimento = dia_vencimento
    matricula.ativo = True
    matricula.save()
    iniciar_cobranca(matricula, primeiro_vencimento)
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
