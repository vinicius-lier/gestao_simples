from calendar import monthrange
from datetime import date, timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from matriculas.models import Matricula
from financeiro.models import Mensalidade

# A rotina diária cria a mensalidade do mês seguinte quando o vencimento
# dela cai dentro desta janela — a tempo do lembrete de 5 dias antes.
ANTECEDENCIA_MES_SEGUINTE_DIAS = 7


def _vencimento(matricula, ano, mes):
    """Vencimento da competência: o dia de vencimento da matrícula (ou o
    último dia do mês), exceto no mês do primeiro vencimento, que vence na
    data escolhida por quem criou ou ativou a matrícula."""
    primeiro = matricula.primeiro_vencimento
    if primeiro is not None and (primeiro.year, primeiro.month) == (ano, mes):
        return primeiro
    ultimo_dia_mes = monthrange(ano, mes)[1]
    return date(ano, mes, min(matricula.dia_vencimento, ultimo_dia_mes))


def _inicio_da_cobranca(matricula):
    """Nenhuma mensalidade da matrícula vence antes desta data."""
    return matricula.primeiro_vencimento or matricula.data_inicio


def _mes_seguinte(ano, mes):
    return (ano + 1, 1) if mes == 12 else (ano, mes + 1)


def proximo_vencimento(matricula, a_partir_de=None):
    """O primeiro dia de vencimento da matrícula a partir de hoje (ou da
    data de início, se ela for futura). É a sugestão de 1º vencimento
    quando quem cria ou ativa a matrícula não escolhe outra data."""
    a_partir_de = a_partir_de or max(matricula.data_inicio, timezone.localdate())
    ano, mes = a_partir_de.year, a_partir_de.month
    vencimento = date(ano, mes, min(matricula.dia_vencimento, monthrange(ano, mes)[1]))
    if vencimento < a_partir_de:
        ano, mes = _mes_seguinte(ano, mes)
        vencimento = date(ano, mes, min(matricula.dia_vencimento, monthrange(ano, mes)[1]))
    return vencimento


def _criar_ou_obter_mensalidade(matricula, ano, mes):
    # Bolsa integral (valor zero) fica registrada como isenta: aparece no
    # histórico do aluno, mas não entra na régua de cobrança nem gera Pix.
    return Mensalidade.objects.get_or_create(
        matricula=matricula,
        competencia=date(ano, mes, 1),
        defaults={
            "academia": matricula.academia,
            "valor": matricula.valor_mensalidade,
            "vencimento": _vencimento(matricula, ano, mes),
            "status": "pendente" if matricula.valor_mensalidade > 0 else "isenta",
        },
    )


def gerar_mensalidades(ano, mes, vencimento_ate=None):
    """Gera as mensalidades da competência para as matrículas ativas de
    alunos ativos, em academias ativas. Idempotente. Aluno trancado ou
    inativo não gera mensalidade nova; as que já estavam em aberto ficam
    como estão.

    Nada vence antes do 1º vencimento da matrícula (mesma regra de
    ``gerar_mensalidade_inicial``); em matrículas antigas, sem ele, antes
    da data de início — quem entra no dia 20 com vencimento no dia 10
    começa a pagar no mês seguinte.

    ``vencimento_ate`` (opcional) limita às mensalidades que vencem até essa
    data — usado pela rotina diária para antecipar o mês seguinte."""
    competencia = date(ano, mes, 1)
    fim_mes = date(ano, mes, monthrange(ano, mes)[1])

    matriculas = Matricula.objects.filter(
        ativo=True,
        atleta__status="ativo",
        academia__ativo=True,
        data_inicio__lte=fim_mes,
    ).filter(
        Q(data_fim__isnull=True) | Q(data_fim__gte=competencia)
    )

    criadas = 0
    existentes = 0

    for matricula in matriculas:
        vencimento = _vencimento(matricula, ano, mes)
        if vencimento < _inicio_da_cobranca(matricula):
            continue
        if vencimento_ate is not None and vencimento > vencimento_ate:
            continue

        _mensalidade, criada = _criar_ou_obter_mensalidade(matricula, ano, mes)
        if criada:
            criadas += 1
        else:
            existentes += 1

    return {
        "criadas": criadas,
        "existentes": existentes,
    }


def gerar_mensalidades_do_dia(hoje=None):
    """Rotina diária (roda junto com os lembretes): garante as mensalidades
    do mês corrente e antecipa as do mês seguinte que vencem nos próximos
    ``ANTECEDENCIA_MES_SEGUINTE_DIAS`` dias. Idempotente — pode rodar
    várias vezes no mesmo dia."""
    hoje = hoje or timezone.localdate()
    resultado = gerar_mensalidades(hoje.year, hoje.month)

    limite = hoje + timedelta(days=ANTECEDENCIA_MES_SEGUINTE_DIAS)
    if (limite.year, limite.month) != (hoje.year, hoje.month):
        seguinte = gerar_mensalidades(limite.year, limite.month, vencimento_ate=limite)
        resultado = {chave: resultado[chave] + seguinte[chave] for chave in resultado}

    return resultado


def gerar_mensalidade_inicial(matricula):
    """Garante a 1ª mensalidade da matrícula — sem esperar o próximo disparo
    de ``gerar_mensalidades``. Idempotente (get_or_create): chamar de novo
    numa matrícula que já tem a mensalidade não duplica."""
    inicio = _inicio_da_cobranca(matricula)
    ano, mes = inicio.year, inicio.month
    if _vencimento(matricula, ano, mes) < inicio:
        ano, mes = _mes_seguinte(ano, mes)

    mensalidade, _criada = _criar_ou_obter_mensalidade(matricula, ano, mes)
    return mensalidade


def iniciar_cobranca(matricula, primeiro_vencimento=None):
    """A matrícula acabou de ficar ativa (cadastro, ativação do convite,
    reativação ou volta do aluno trancado): grava o 1º vencimento — o
    escolhido por quem criou ou ativou a matrícula ou, sem escolha, o
    próximo dia de vencimento a partir de hoje — e cria a 1ª mensalidade.

    Nada vence antes dele: quem já treinava antes de entrar no sistema não
    recebe mensalidade retroativa. Aluno que não está ativo tem a data
    registrada, mas só é cobrado quando voltar."""
    matricula.primeiro_vencimento = primeiro_vencimento or proximo_vencimento(matricula)
    matricula.save(update_fields=["primeiro_vencimento"])
    if matricula.atleta.status != "ativo":
        return None
    return gerar_mensalidade_inicial(matricula)


def registrar_pagamento(mensalidade, forma_pagamento="", quando=None):
    """Marca uma mensalidade em aberto como paga manualmente (conferência de
    banco, dinheiro em mãos etc.). O mesmo caminho é usado pelo webhook da
    Woovi quando o pagamento é confirmado automaticamente pelo gateway.

    Confere a situação atual no banco, com a linha travada: uma tela
    desatualizada não sobrescreve um pagamento já registrado (por exemplo,
    o Pix que caiu enquanto a lista estava aberta)."""
    with transaction.atomic():
        status = (
            Mensalidade.objects.select_for_update()
            .values_list("status", flat=True)
            .get(pk=mensalidade.pk)
        )
        if status == "paga":
            raise ValueError("Esta mensalidade já está paga.")
        if status not in ("pendente", "vencida"):
            rotulo = dict(Mensalidade.STATUS)[status].lower()
            raise ValueError(f"Uma mensalidade {rotulo} não pode ser paga.")

        mensalidade.status = "paga"
        mensalidade.forma_pagamento = forma_pagamento
        mensalidade.pago_em = quando or timezone.now()
        mensalidade.save(update_fields=["status", "forma_pagamento", "pago_em"])
    return mensalidade


def encerrar_mensalidade(mensalidade, status):
    """Cancela ou isenta uma mensalidade em aberto (aluno desistiu, bolsa
    etc.). Se há um Pix vigente na Woovi, ele é excluído antes — senão a
    família ainda conseguiria pagar pelo código antigo. A mensalidade sai
    da régua de lembretes, que só olha as em aberto."""
    if status not in ("cancelada", "isenta"):
        raise ValueError("Escolha cancelar ou isentar.")
    if mensalidade.status not in ("pendente", "vencida"):
        raise ValueError("Só mensalidades em aberto podem ser canceladas ou isentadas.")

    from integracoes.woovi.services import remover_cobranca_pix

    remover_cobranca_pix(mensalidade)
    mensalidade.status = status
    mensalidade.save(update_fields=["status"])
    return mensalidade


def resumo_financeiro(academia, competencia):
    """Indicadores do painel: previsto, recebido, a receber e atrasado
    para o mês de referência (competência) informado."""
    Mensalidade.objects.filter(academia=academia).marcar_vencidas()

    do_mes = Mensalidade.objects.filter(academia=academia, competencia=competencia).exclude(
        status__in=("cancelada", "isenta")
    )
    previsto = sum((m.valor for m in do_mes), start=0)
    recebido = sum((m.valor for m in do_mes if m.status == "paga"), start=0)
    atrasado = sum((m.valor for m in do_mes if m.status == "vencida"), start=0)
    a_receber = previsto - recebido - atrasado
    inadimplencia = (atrasado / previsto * 100) if previsto else 0

    return {
        "previsto": previsto,
        "recebido": recebido,
        "a_receber": a_receber,
        "atrasado": atrasado,
        "inadimplencia": inadimplencia,
    }