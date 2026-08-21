from calendar import monthrange
from datetime import date

from django.db.models import Q

from matriculas.models import Matricula
from financeiro.models import Mensalidade

def gerar_mensalidades(ano, mes):
    competencia = date(ano, mes, 1)

    ultimo_dia_mes = monthrange(ano, mes)[1]
    fim_mes = date(ano, mes, ultimo_dia_mes)

    matriculas = Matricula.objects.filter(
        ativo=True,
        data_inicio__lte=fim_mes,
    ).filter(
        Q(data_fim__isnull=True) | Q(data_fim__gte=competencia)
    )

    criadas = 0
    existentes = 0

    for matricula in matriculas:
        dia_vencimento = min(
            matricula.dia_vencimento,
            ultimo_dia_mes,
        )

        vencimento = date(
            ano,
            mes,
            dia_vencimento,
        )

        mensalidade, criada = Mensalidade.objects.get_or_create(
            matricula=matricula,
            competencia=competencia,
            defaults={
                "academia": matricula.academia,
                "valor": matricula.valor_mensalidade,
                "vencimento": vencimento,
                "status": "pendente",
            },
        )

        if criada:
            criadas += 1
        else:
            existentes += 1

    return {
        "criadas": criadas,
        "existentes": existentes,
    }