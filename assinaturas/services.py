"""Faturas da assinatura do sistema: geração mensal (rotina diária), aviso de
pagamento pela academia e confirmação pela plataforma."""
from calendar import monthrange
from datetime import date, timedelta

from django.conf import settings
from django.utils import timezone

from monitoramento.alertas import enviar_alerta

from .models import Assinatura, FaturaAssinatura
from .pix import br_code_estatico

LINK_ADMIN_FATURAS = "/admin/assinaturas/faturaassinatura/"


def gerar_faturas(hoje=None):
    """Garante a fatura do mês corrente de cada assinatura ativa de academia
    ativa, a partir do início da cobrança. Idempotente."""
    hoje = hoje or timezone.localdate()
    criadas = 0
    for assinatura in Assinatura.objects.filter(ativa=True, academia__ativo=True):
        vencimento = date(hoje.year, hoje.month, min(assinatura.dia_vencimento, monthrange(hoje.year, hoje.month)[1]))
        if vencimento < assinatura.inicio:
            continue
        _, criada = FaturaAssinatura.objects.get_or_create(
            assinatura=assinatura,
            competencia=date(hoje.year, hoje.month, 1),
            defaults={"valor": assinatura.valor_mensal, "vencimento": vencimento},
        )
        criadas += criada
    return criadas


def avisar_faturas_vencidas(hoje=None):
    """Avisa a plataforma no Discord das faturas vencidas (uma vez por semana
    cada). Não fala com a academia: ela vê a faixa no portal."""
    hoje = hoje or timezone.localdate()
    vencidas = FaturaAssinatura.objects.filter(
        status=FaturaAssinatura.ABERTA, vencimento__lt=hoje,
    ).select_related("assinatura__academia")
    for fatura in vencidas:
        academia = fatura.assinatura.academia
        dias = (hoje - fatura.vencimento).days
        enviar_alerta(
            f"📅 Assinatura vencida: {academia.nome_fantasia or academia.nome}",
            f"A fatura de **{fatura.competencia:%m/%Y}** do sistema (R$ {fatura.valor}) venceu em "
            f"{fatura.vencimento:%d/%m/%Y}, há {dias} dia(s), e ainda não foi confirmada.",
            f"assinatura:vencida:{fatura.pk}",
            nivel="aviso",
            intervalo=timedelta(days=7),
            campos=[
                ("O que isso significa", "A academia ainda não pagou a mensalidade do sistema, ou pagou e você não confirmou."),
                ("O que fazer", "Confira no seu banco se o Pix chegou. Se chegou, confirme a fatura no admin "
                                f"({LINK_ADMIN_FATURAS}, ação **Confirmar pagamento**). Se não, fale com a academia."),
            ],
        )
    return len(vencidas)


def pix_da_fatura(fatura):
    """Pix copia e cola da fatura para a chave da plataforma, ou "" se a
    chave não estiver configurada."""
    chave = getattr(settings, "PLATAFORMA_PIX_CHAVE", "")
    if not chave:
        return ""
    return br_code_estatico(
        chave,
        getattr(settings, "PLATAFORMA_PIX_NOME", "") or "Gestao Simples",
        getattr(settings, "PLATAFORMA_PIX_CIDADE", "") or "Rio de Janeiro",
        valor=fatura.valor,
        txid=fatura.txid,
    )


def informar_pagamento(fatura):
    """A academia avisou, pelo portal, que pagou. A fatura só vira paga
    quando a plataforma confirmar no admin."""
    if fatura.status != FaturaAssinatura.ABERTA:
        raise ValueError("Esta fatura não está em aberto.")
    fatura.pagamento_informado_em = timezone.now()
    fatura.save(update_fields=["pagamento_informado_em"])
    academia = fatura.assinatura.academia
    enviar_alerta(
        f"💰 Pagamento informado: {academia.nome_fantasia or academia.nome}",
        f"A academia avisou que pagou a fatura de **{fatura.competencia:%m/%Y}** do sistema: "
        f"**R$ {fatura.valor}** (identificador do Pix: `{fatura.txid}`).",
        f"assinatura:informada:{fatura.pk}",
        nivel="ok",
        campos=[
            ("O que fazer", "Confira no seu banco se o Pix chegou e confirme a fatura no admin "
                            f"({LINK_ADMIN_FATURAS}, ação **Confirmar pagamento**)."),
        ],
    )
    return fatura


def confirmar_pagamento(fatura):
    fatura.status = FaturaAssinatura.PAGA
    fatura.pago_em = timezone.now()
    fatura.save(update_fields=["status", "pago_em"])
    return fatura
