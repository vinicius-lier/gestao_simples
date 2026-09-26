from calendar import monthrange
from datetime import date, timedelta

from django.db.models import Q
from django.utils import timezone

from matriculas.models import Matricula
from financeiro.models import Mensalidade

# A rotina diária cria a mensalidade do mês seguinte quando o vencimento
# dela cai dentro desta janela — a tempo do lembrete de 5 dias antes.
ANTECEDENCIA_MES_SEGUINTE_DIAS = 7


def _vencimento(matricula, ano, mes):
    ultimo_dia_mes = monthrange(ano, mes)[1]
    return date(ano, mes, min(matricula.dia_vencimento, ultimo_dia_mes))


def _criar_ou_obter_mensalidade(matricula, ano, mes):
    return Mensalidade.objects.get_or_create(
        matricula=matricula,
        competencia=date(ano, mes, 1),
        defaults={
            "academia": matricula.academia,
            "valor": matricula.valor_mensalidade,
            "vencimento": _vencimento(matricula, ano, mes),
            "status": "pendente",
        },
    )


def gerar_mensalidades(ano, mes, vencimento_ate=None):
    """Gera as mensalidades da competência para as matrículas ativas de
    alunos ativos. Idempotente.

    A 1ª mensalidade de uma matrícula é a do primeiro vencimento a partir
    da data de início (mesma regra de ``gerar_mensalidade_inicial``): quem
    entra no dia 20 com vencimento no dia 10 começa a pagar no mês seguinte,
    e não recebe uma cobrança que já nasce vencida.

    ``vencimento_ate`` (opcional) limita às mensalidades que vencem até essa
    data — usado pela rotina diária para antecipar o mês seguinte."""
    competencia = date(ano, mes, 1)
    fim_mes = date(ano, mes, monthrange(ano, mes)[1])

    matriculas = Matricula.objects.filter(
        ativo=True,
        atleta__status="ativo",
        data_inicio__lte=fim_mes,
    ).filter(
        Q(data_fim__isnull=True) | Q(data_fim__gte=competencia)
    )

    criadas = 0
    existentes = 0

    for matricula in matriculas:
        vencimento = _vencimento(matricula, ano, mes)
        if vencimento < matricula.data_inicio:
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
    hoje = hoje or date.today()
    resultado = gerar_mensalidades(hoje.year, hoje.month)

    limite = hoje + timedelta(days=ANTECEDENCIA_MES_SEGUINTE_DIAS)
    if (limite.year, limite.month) != (hoje.year, hoje.month):
        seguinte = gerar_mensalidades(limite.year, limite.month, vencimento_ate=limite)
        resultado = {chave: resultado[chave] + seguinte[chave] for chave in resultado}

    return resultado


def gerar_mensalidade_inicial(matricula):
    """Garante a mensalidade do vencimento mais próximo da data de início
    da matrícula assim que ela fica ativa — sem esperar o próximo disparo
    mensal de ``gerar_mensalidades``. Idempotente (get_or_create): chamar
    de novo numa matrícula que já tem a mensalidade não duplica."""
    data_inicio = matricula.data_inicio
    if data_inicio.day <= matricula.dia_vencimento:
        ano, mes = data_inicio.year, data_inicio.month
    else:
        ano, mes = data_inicio.year, data_inicio.month + 1
        if mes > 12:
            ano, mes = ano + 1, 1

    mensalidade, _criada = _criar_ou_obter_mensalidade(matricula, ano, mes)
    return mensalidade


def registrar_pagamento(mensalidade, forma_pagamento="", quando=None):
    """Marca uma mensalidade como paga manualmente (conferência de banco,
    dinheiro em mãos etc.). O mesmo caminho é usado pelo webhook do Asaas
    quando o pagamento é confirmado automaticamente pelo gateway."""
    if mensalidade.status == "cancelada":
        raise ValueError("Uma mensalidade cancelada não pode ser paga.")

    mensalidade.status = "paga"
    mensalidade.forma_pagamento = forma_pagamento
    mensalidade.pago_em = quando or timezone.now()
    mensalidade.save(update_fields=["status", "forma_pagamento", "pago_em"])
    return mensalidade


def encerrar_mensalidade(mensalidade, status):
    """Cancela ou isenta uma mensalidade em aberto (aluno desistiu, bolsa
    etc.). Se já existe cobrança no Asaas, ela é excluída lá antes — senão
    a família ainda conseguiria pagar pelo link antigo. A mensalidade sai
    da régua de lembretes, que só olha as em aberto."""
    if status not in ("cancelada", "isenta"):
        raise ValueError("Escolha cancelar ou isentar.")
    if mensalidade.status not in ("pendente", "vencida"):
        raise ValueError("Só mensalidades em aberto podem ser canceladas ou isentadas.")

    from integracoes.asaas.services import remover_cobranca_asaas

    remover_cobranca_asaas(mensalidade)
    mensalidade.status = status
    mensalidade.save(update_fields=["status"])
    return mensalidade


def resumo_financeiro(academia, competencia):
    """Indicadores do painel: previsto, recebido, a receber e atrasado
    para o mês de referência (competência) informado."""
    Mensalidade.objects.filter(academia=academia).marcar_vencidas()

    do_mes = Mensalidade.objects.filter(academia=academia, competencia=competencia).exclude(status="cancelada")
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