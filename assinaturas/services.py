"""Assinatura do sistema: a mensalidade que a academia paga à plataforma.

Rotina diária (``rotina_diaria``, chamada por ``enviar_lembretes_cobranca``):
  1. gera a fatura do mês de cada assinatura a partir do 1º vencimento
     (competência mensal + dia de vencimento; idempotente) e já cria o Pix;
  2. marca como atrasadas as faturas vencidas;
  3. suspende a assinatura no dia seguinte ao fim da tolerância;
  4. avisa a plataforma no Discord.

A suspensão também é conferida a cada acesso ao painel
(``atualizar_situacao`` em ``academia_required``), para valer desde a
meia-noite. O pagamento chega pelo webhook da Woovi
(``registrar_pagamento_pix``), que dá baixa e reativa a assinatura suspensa
por atraso. Suspender não apaga nada: só fecha as telas operacionais.
"""
import logging
import re
from calendar import monthrange
from datetime import date, timedelta

from django.db import transaction
from django.utils import timezone

from monitoramento.alertas import enviar_alerta

from .models import Assinatura, FaturaAssinatura

logger = logging.getLogger(__name__)

PREFIXO_CORRELATION = "assinatura-"
VALIDADE_PIX = timedelta(days=30)
# Pix que expira dentro desta janela é trocado por um novo antes de ser mostrado.
MARGEM_RENOVACAO_PIX = timedelta(hours=1)
LINK_ADMIN = "/admin/assinaturas/"

_CORRELATION_RE = re.compile(rf"^{PREFIXO_CORRELATION}(\d+)-")


def _nome(academia):
    return academia.nome_fantasia or academia.nome


# ------------------------------------------------------------------ geração
def vencimento_do_mes(assinatura, ano, mes):
    return date(ano, mes, min(assinatura.dia_vencimento, monthrange(ano, mes)[1]))


def primeiro_vencimento(assinatura):
    """Nada vence antes disto: o escolhido na assinatura ou, sem ele, o
    primeiro dia de vencimento a partir do início do serviço."""
    if assinatura.primeiro_vencimento:
        return assinatura.primeiro_vencimento
    inicio = assinatura.inicio
    vencimento = vencimento_do_mes(assinatura, inicio.year, inicio.month)
    if vencimento < inicio:
        ano, mes = (inicio.year + 1, 1) if inicio.month == 12 else (inicio.year, inicio.month + 1)
        vencimento = vencimento_do_mes(assinatura, ano, mes)
    return vencimento


def proximo_vencimento_previsto(assinatura, hoje=None):
    """O próximo dia de vencimento a partir de hoje, nunca antes do 1º
    vencimento — para a tela mostrar antes de a fatura existir."""
    hoje = hoje or timezone.localdate()
    vencimento = vencimento_do_mes(assinatura, hoje.year, hoje.month)
    if vencimento < hoje:
        ano, mes = (hoje.year + 1, 1) if hoje.month == 12 else (hoje.year, hoje.month + 1)
        vencimento = vencimento_do_mes(assinatura, ano, mes)
    return max(vencimento, primeiro_vencimento(assinatura))


def gerar_faturas(hoje=None, criar_pix=True):
    """Garante a fatura do mês corrente de cada assinatura não cancelada de
    academia ativa — só a partir do 1º vencimento. Uma por competência (o
    banco garante); o Pix de cada fatura em aberto é criado junto (sem
    duplicar). Devolve quantas faturas foram criadas."""
    hoje = hoje or timezone.localdate()
    criadas = 0
    assinaturas = Assinatura.objects.exclude(status=Assinatura.CANCELADA).filter(academia__ativo=True)
    for assinatura in assinaturas.select_related("academia"):
        vencimento = vencimento_do_mes(assinatura, hoje.year, hoje.month)
        if vencimento < primeiro_vencimento(assinatura):
            continue
        fatura, criada = FaturaAssinatura.objects.get_or_create(
            assinatura=assinatura,
            competencia=date(hoje.year, hoje.month, 1),
            tipo=FaturaAssinatura.MENSALIDADE,
            defaults={"valor": assinatura.valor_mensal, "vencimento": vencimento},
        )
        criadas += criada
        if criar_pix and fatura.em_aberto:
            _garantir_pix_silencioso(fatura)
    return criadas


# ---------------------------------------------------------------------- Pix
def fatura_a_pagar(assinatura):
    """A fatura em aberto que vence primeiro (a que o botão Pagar paga)."""
    return assinatura.faturas.filter(status__in=FaturaAssinatura.EM_ABERTO).order_by("vencimento", "pk").first()


def _serve(fatura):
    return (
        bool(fatura.br_code) and fatura.pix_expira_em is not None
        and fatura.pix_expira_em > timezone.now() + MARGEM_RENOVACAO_PIX
    )


def garantir_pix(fatura):
    """Devolve a fatura com um Pix vigente, criando a cobrança na Woovi se
    preciso. O valor é sempre o da fatura no banco. Seguro contra cliques
    simultâneos (linha travada) e contra repetição depois de uma falha no
    meio do caminho: o correlationID de cada tentativa é fixo e a Woovi
    devolve a cobrança já criada em vez de duplicar."""
    from integracoes.woovi.client import WooviClient
    from integracoes.woovi.exceptions import WooviInvalidResponseError
    from integracoes.woovi.services import valor_em_centavos

    if not fatura.em_aberto:
        raise ValueError("Esta fatura não está em aberto.")
    if _serve(fatura):
        return fatura

    with transaction.atomic():
        travada = FaturaAssinatura.objects.select_for_update().select_related("assinatura__academia").get(pk=fatura.pk)
        if not travada.em_aberto:
            raise ValueError("Esta fatura não está em aberto.")
        if _serve(travada):
            return travada

        # Pix novo só quando o anterior venceu: a tentativa é contada pelo
        # correlationID gravado (o de uma tentativa que não chegou a ser
        # gravada é refeito igual, e a Woovi devolve a mesma cobrança).
        tentativa = 1
        if travada.correlation_id:
            anterior = travada.correlation_id.rsplit("-", 1)[-1]
            tentativa = int(anterior) + 1 if anterior.isdigit() else 2
        academia = travada.assinatura.academia
        cliente = {"name": academia.nome}
        cnpj = re.sub(r"\D", "", academia.cnpj or "")
        if len(cnpj) == 14:
            cliente["taxID"] = cnpj
        if academia.email:
            cliente["email"] = academia.email
        criada = WooviClient().criar_cobranca(
            correlation_id=f"{PREFIXO_CORRELATION}{travada.pk}-{tentativa}",
            valor_centavos=valor_em_centavos(travada.valor),
            comentario=f"{travada.referencia} do sistema - {_nome(academia)}"[:140],
            expira_em_segundos=int(VALIDADE_PIX.total_seconds()),
            cliente=cliente if len(cliente) > 1 else None,
        )
        if not criada.correlation_id or not criada.br_code:
            raise WooviInvalidResponseError("A resposta da Woovi não contém o Pix da cobrança.")
        travada.correlation_id = criada.correlation_id
        travada.woovi_charge_id = criada.transaction_id
        travada.br_code = criada.br_code
        travada.pix_expira_em = criada.expira_em or timezone.now() + VALIDADE_PIX
        travada.save(update_fields=["correlation_id", "woovi_charge_id", "br_code", "pix_expira_em", "atualizada_em"])
        return travada


def _garantir_pix_silencioso(fatura):
    from integracoes.woovi.exceptions import WooviError

    try:
        return garantir_pix(fatura)
    except (ValueError, WooviError) as erro:
        # Sem Pix agora, a academia gera na hora de pagar; a plataforma fica sabendo.
        logger.warning("Pix da fatura da assinatura %s não criado: %s", fatura.pk, erro)
        return None


def fatura_do_correlation_id(correlation_id):
    correspondencia = _CORRELATION_RE.match(correlation_id or "")
    if not correspondencia:
        return None
    return FaturaAssinatura.objects.filter(pk=correspondencia.group(1)).first()


# ------------------------------------------------------------------ pagamento
def registrar_pagamento_pix(correlation_id, *, pago_em=None, charge_id=""):
    """Chamado pelo webhook da Woovi (cobrança paga). Marca a fatura como
    paga — uma vez só — e reativa a assinatura suspensa por atraso.
    Devolve True se registrou agora; False se já estava paga ou se a
    fatura não existe (o webhook grava o motivo)."""
    fatura = fatura_do_correlation_id(correlation_id)
    if fatura is None:
        return False
    with transaction.atomic():
        fatura = FaturaAssinatura.objects.select_for_update().select_related("assinatura__academia").get(pk=fatura.pk)
        if fatura.status == FaturaAssinatura.PAGA:
            return False
        if fatura.status == FaturaAssinatura.CANCELADA:
            _alertar(
                f"⚠️ Pix pago de fatura cancelada: {_nome(fatura.assinatura.academia)}",
                f"A fatura **{fatura.referencia}** (R$ {fatura.valor}) estava cancelada e foi paga pelo Pix "
                f"`{correlation_id}`. Confira e devolva ou reative a fatura.",
                f"assinatura:paga-cancelada:{fatura.pk}", nivel="aviso",
            )
        fatura.status = FaturaAssinatura.PAGA
        fatura.pago_em = pago_em or timezone.now()
        fatura.woovi_charge_id = charge_id or fatura.woovi_charge_id
        fatura.save(update_fields=["status", "pago_em", "woovi_charge_id", "atualizada_em"])
        atualizar_situacao(fatura.assinatura)
    academia = fatura.assinatura.academia
    _alertar(
        f"💰 Assinatura paga: {_nome(academia)}",
        f"A **{fatura.referencia}** do sistema (R$ {fatura.valor}) foi paga por Pix.",
        f"assinatura:paga:{fatura.pk}", nivel="ok",
    )
    return True


def confirmar_pagamento_manual(fatura, quando=None):
    """Baixa feita no admin, para pagamento recebido por fora do Pix
    (transferência, dinheiro). Também reativa a assinatura."""
    with transaction.atomic():
        fatura = FaturaAssinatura.objects.select_for_update().select_related("assinatura").get(pk=fatura.pk)
        if not fatura.em_aberto:
            return False
        fatura.status = FaturaAssinatura.PAGA
        fatura.pago_em = quando or timezone.now()
        fatura.save(update_fields=["status", "pago_em", "atualizada_em"])
        atualizar_situacao(fatura.assinatura)
    _remover_pix_silencioso(fatura)
    return True


def cancelar_fatura(fatura):
    if not fatura.em_aberto:
        return False
    fatura.status = FaturaAssinatura.CANCELADA
    fatura.save(update_fields=["status", "atualizada_em"])
    atualizar_situacao(fatura.assinatura)
    _remover_pix_silencioso(fatura)
    return True


def _remover_pix_silencioso(fatura):
    """Tira do ar o Pix de uma fatura que não deve mais ser paga."""
    from integracoes.woovi.client import WooviClient
    from integracoes.woovi.exceptions import WooviError

    if not fatura.correlation_id or not fatura.pix_vigente:
        return
    try:
        WooviClient().remover_cobranca(fatura.correlation_id)
    except WooviError as erro:
        logger.warning("Pix %s da assinatura não foi removido: %s", fatura.correlation_id, erro)


def conferir_pagamento(fatura):
    """Rede de segurança para webhook perdido: pergunta à Woovi se o Pix em
    vigor já foi pago e, se sim, dá baixa pelo mesmo caminho do webhook."""
    from integracoes.woovi.client import WooviClient
    from integracoes.woovi.exceptions import WooviError

    if not fatura.em_aberto or not fatura.correlation_id:
        return False
    try:
        remota = WooviClient().obter_cobranca(fatura.correlation_id)
    except WooviError as erro:
        logger.warning("Consulta do Pix %s da assinatura falhou: %s", fatura.correlation_id, erro)
        return False
    if remota.status != "COMPLETED":
        return False
    return registrar_pagamento_pix(
        fatura.correlation_id, pago_em=remota.pago_em, charge_id=remota.transaction_id,
    )


# ------------------------------------------------------- atraso e suspensão
def atualizar_situacao(assinatura, hoje=None):
    """Aplica as regras do dia: fatura vencida vira atrasada; passada a
    tolerância, a assinatura é suspensa; sem fatura fora da tolerância, a
    suspensa por atraso volta a ativa. Só grava o que mudou. Devolve a
    assinatura atualizada."""
    hoje = hoje or timezone.localdate()
    em_aberto = assinatura.faturas.filter(
        status__in=FaturaAssinatura.EM_ABERTO, tipo=FaturaAssinatura.MENSALIDADE,
    )
    em_aberto.filter(status=FaturaAssinatura.PENDENTE, vencimento__lt=hoje).update(
        status=FaturaAssinatura.ATRASADA, atualizada_em=timezone.now(),
    )
    limite = hoje - timedelta(days=assinatura.dias_tolerancia)
    fora_da_tolerancia = em_aberto.filter(vencimento__lt=limite).order_by("vencimento").first()

    if assinatura.status == Assinatura.ATIVA and fora_da_tolerancia is not None:
        assinatura.status = Assinatura.SUSPENSA
        assinatura.motivo_suspensao = Assinatura.INADIMPLENCIA
        assinatura.suspensa_em = timezone.now()
        assinatura.save(update_fields=["status", "motivo_suspensao", "suspensa_em"])
        _alertar(
            f"⛔ Assinatura suspensa: {_nome(assinatura.academia)}",
            f"A **{fora_da_tolerancia.referencia}** (R$ {fora_da_tolerancia.valor}) venceu em "
            f"{fora_da_tolerancia.vencimento:%d/%m/%Y} e passou da tolerância de "
            f"{assinatura.dias_tolerancia} dias. O painel da academia foi bloqueado (nada foi apagado); "
            "volta sozinho quando o Pix for pago.",
            f"assinatura:suspensa:{assinatura.pk}:{fora_da_tolerancia.pk}", nivel="erro",
        )
    elif assinatura.suspensa_por_atraso and fora_da_tolerancia is None:
        assinatura.status = Assinatura.ATIVA
        assinatura.motivo_suspensao = ""
        assinatura.suspensa_em = None
        assinatura.save(update_fields=["status", "motivo_suspensao", "suspensa_em"])
    return assinatura


def avisar_faturas_atrasadas(hoje=None):
    """Avisa a plataforma no Discord das faturas atrasadas (uma vez por
    semana cada)."""
    hoje = hoje or timezone.localdate()
    atrasadas = FaturaAssinatura.objects.filter(
        status__in=FaturaAssinatura.EM_ABERTO, vencimento__lt=hoje,
    ).select_related("assinatura__academia")
    for fatura in atrasadas:
        academia = fatura.assinatura.academia
        _alertar(
            f"📅 Assinatura atrasada: {_nome(academia)}",
            f"A **{fatura.referencia}** do sistema (R$ {fatura.valor}) venceu em "
            f"{fatura.vencimento:%d/%m/%Y}, há {(hoje - fatura.vencimento).days} dia(s). "
            f"Sem pagamento, o painel é suspenso em {fatura.suspensao_em:%d/%m/%Y}.",
            f"assinatura:atrasada:{fatura.pk}", nivel="aviso", intervalo=timedelta(days=7),
            campos=[("O que fazer", "O pagamento por Pix dá baixa sozinho. Se a academia pagou por fora, "
                                    f"confirme a fatura no admin ({LINK_ADMIN}).")],
        )
    return len(atrasadas)


def rotina_diaria(hoje=None):
    """Tudo o que a assinatura precisa por dia. Idempotente."""
    hoje = hoje or timezone.localdate()
    criadas = gerar_faturas(hoje)
    for assinatura in Assinatura.objects.exclude(status=Assinatura.CANCELADA).select_related("academia"):
        atualizar_situacao(assinatura, hoje)
    avisar_faturas_atrasadas(hoje)
    return criadas


def _alertar(titulo, texto, chave, **kwargs):
    try:
        enviar_alerta(titulo, texto, chave, **kwargs)
    except Exception:  # o alerta nunca derruba a cobrança
        logger.exception("Alerta da assinatura não enviado: %s", titulo)


# ------------------------------------------------------------------- acesso
def assinatura_da(academia):
    return Assinatura.objects.filter(academia=academia).select_related("academia").first()
