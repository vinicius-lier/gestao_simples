from calendar import monthrange
from datetime import date

from django.db.models import Q
from django.utils import timezone

from matriculas.models import Matricula
from financeiro.models import Mensalidade

def _criar_ou_obter_mensalidade(matricula, ano, mes):
    competencia = date(ano, mes, 1)
    ultimo_dia_mes = monthrange(ano, mes)[1]
    vencimento = date(ano, mes, min(matricula.dia_vencimento, ultimo_dia_mes))

    return Mensalidade.objects.get_or_create(
        matricula=matricula,
        competencia=competencia,
        defaults={
            "academia": matricula.academia,
            "valor": matricula.valor_mensalidade,
            "vencimento": vencimento,
            "status": "pendente",
        },
    )


def gerar_mensalidades(ano, mes):
    competencia = date(ano, mes, 1)
    fim_mes = date(ano, mes, monthrange(ano, mes)[1])

    matriculas = Matricula.objects.filter(
        ativo=True,
        data_inicio__lte=fim_mes,
    ).filter(
        Q(data_fim__isnull=True) | Q(data_fim__gte=competencia)
    )

    criadas = 0
    existentes = 0

    for matricula in matriculas:
        _mensalidade, criada = _criar_ou_obter_mensalidade(matricula, ano, mes)
        if criada:
            criadas += 1
        else:
            existentes += 1

    return {
        "criadas": criadas,
        "existentes": existentes,
    }


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