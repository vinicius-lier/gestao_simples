"""Repasse automático: transfere o saldo da subconta para a chave Pix da
academia depois de cada pagamento.

Nunca roda dentro do webhook. O webhook só abre o ``Repasse`` PENDENTE; o
comando ``processar_repasses`` (tarefa agendada a cada minuto) faz o resto:

1. reivindica o repasse com um UPDATE condicional (compare-and-swap):
   dois processos rodando juntos nunca pegam o mesmo repasse, e o banco já
   garante um único repasse aberto por conta — logo, nunca dois saques
   concorrentes da mesma subconta;
2. se o pedido anterior terminou em timeout, confere o extrato ANTES de
   pedir de novo (o saque não é idempotente);
3. credita na subconta o LÍQUIDO de cada Pix pago ainda não creditado (o Pix
   é criado sem split — a Woovi não aceita split de 100% — e a taxa da Woovi
   é paga pela academia). O crédito também não é idempotente: timeout marca
   a cobrança e o extrato é conferido antes de repetir;
4. consulta o saldo REAL da subconta — nunca assume que é o valor da
   mensalidade (taxas, pagamentos acumulados);
5. saca todo o saldo disponível;
6. a confirmação chega pelos webhooks MOVEMENT_CONFIRMED/FAILED; repasse
   sem confirmação depois de ``PRAZO_CONFIRMACAO`` é conferido no extrato.

Falhas são retentadas após 1, 5, 15, 60 e 180 minutos (6 tentativas, ~4h20);
depois o repasse vai para REQUER_ATENCAO e a plataforma é alertada.
"""
import logging
import re
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from financeiro.alertas import alertar_plataforma
from financeiro.models import CobrancaPix, ContaRecebimento, Repasse
from integracoes.woovi.client import WooviClient
from integracoes.woovi.exceptions import WooviError, WooviTimeoutError

logger = logging.getLogger(__name__)

ESPERAS_MINUTOS = (1, 5, 15, 60, 180)
MAX_TENTATIVAS = len(ESPERAS_MINUTOS) + 1

# Saldo zerado logo após o pagamento pode ser crédito ainda não lançado na
# subconta: espera algumas tentativas antes de concluir "sem saldo".
TENTATIVAS_AGUARDANDO_SALDO = 3

PRAZO_CONFIRMACAO = timedelta(minutes=30)
FOLGA_RELOGIO = timedelta(minutes=5)

STATUS_SAQUE_CONFIRMADO = {"CONFIRMED", "COMPLETED"}

_URL = re.compile(r"https?://\S+")


def saque_minimo_centavos():
    return int(getattr(settings, "WOOVI_SAQUE_MINIMO_CENTAVOS", 101))


def valor_do_saque(saldo_centavos):
    """Quanto pedir para esvaziar a subconta. Abaixo de R$ 1.000 a Woovi
    cobra a tarifa de saque do próprio saldo, além do valor pedido (pedir
    o saldo inteiro volta "Saldo insuficiente"); a partir daí não há
    tarifa. A tarifa é paga pela academia."""
    if saldo_centavos >= int(getattr(settings, "WOOVI_SAQUE_SEM_TARIFA_CENTAVOS", 100000)):
        return saldo_centavos
    return max(0, saldo_centavos - int(getattr(settings, "WOOVI_TARIFA_SAQUE_CENTAVOS", 100)))


class TaxaDesconhecida(Exception):
    """Pix pago sem a taxa da Woovi: não dá para calcular o líquido."""


def _texto_erro(erro):
    return _URL.sub("[url]", str(erro))[:500]


def _centavos(repasse):
    return int((repasse.valor or Decimal("0")) * 100)


# -------------------------------------------------------------- transições
def _concluir(repasse, end_to_end_id="", observacao=""):
    from integracoes.woovi.services import solicitar_repasse

    with transaction.atomic():
        repasse.status = Repasse.CONCLUIDA
        repasse.concluido_em = timezone.now()
        repasse.reconciliar = False
        repasse.erro = observacao
        if end_to_end_id:
            repasse.end_to_end_id = end_to_end_id
        repasse.save()
        if repasse.saldo_novo_pendente:
            solicitar_repasse(repasse.conta_recebimento)
    return repasse


def _requer_atencao(repasse, motivo):
    repasse.status = Repasse.REQUER_ATENCAO
    repasse.erro = _texto_erro(motivo)
    repasse.save()
    alertar_plataforma(
        "Repasse para a chave Pix da academia requer atenção",
        repasse.erro,
        repasse=repasse.pk,
        academia=repasse.academia_id,
        tentativas=repasse.tentativas,
    )
    return repasse


def _falha(repasse, motivo):
    if repasse.tentativas >= MAX_TENTATIVAS:
        return _requer_atencao(repasse, motivo)
    espera = ESPERAS_MINUTOS[max(repasse.tentativas, 1) - 1]
    repasse.status = Repasse.FALHA
    repasse.erro = _texto_erro(motivo)
    repasse.proxima_tentativa_em = timezone.now() + timedelta(minutes=espera)
    repasse.save()
    logger.warning(
        "Repasse %s falhou (tentativa %s); nova tentativa em %s min: %s",
        repasse.pk, repasse.tentativas, espera, repasse.erro,
    )
    return repasse


def _aguardar_saldo(repasse):
    """Sem saldo ainda: volta a PENDENTE sem contar como falha."""
    espera = ESPERAS_MINUTOS[max(repasse.tentativas, 1) - 1]
    repasse.status = Repasse.PENDENTE
    repasse.proxima_tentativa_em = timezone.now() + timedelta(minutes=espera)
    repasse.save()
    return repasse


# ------------------------------------------------------------------ extrato
def _saque_no_extrato(client, repasse):
    """(saque, estorno) do extrato que correspondem a este repasse: mesmo
    valor, lançados depois do pedido. Cada item é o lançamento ou None."""
    inicio = (repasse.processado_em or repasse.criado_em) - FOLGA_RELOGIO
    valor = _centavos(repasse)
    saque = estorno = None
    for lancamento in client.extrato_subconta(repasse.pix_key_destino):
        if lancamento.valor_centavos != valor or (lancamento.momento and lancamento.momento < inicio):
            continue
        if lancamento.operacao == "WITHDRAWAL" and saque is None:
            saque = lancamento
        elif lancamento.operacao == "WITHDRAWAL_REVERSAL" and estorno is None:
            estorno = lancamento
    return saque, estorno


# ------------------------------------------------------------------ crédito
def _credito_no_extrato(client, cobranca, centavos):
    inicio = (cobranca.pago_em or cobranca.criada_em) - FOLGA_RELOGIO
    for lancamento in client.extrato_subconta(cobranca.conta_recebimento.pix_key):
        if (
            lancamento.operacao in ("CREDIT", "TRANSFER_CREDIT")
            and lancamento.valor_centavos == centavos
            and (lancamento.momento is None or lancamento.momento >= inicio)
        ):
            return True
    return False


def _creditar_pagamentos(client, repasse):
    """Credita na subconta o líquido (pago menos a taxa da Woovi) de cada
    Pix pago desta conta ainda não creditado. Timeout marca
    ``credito_incerto`` e propaga: na próxima tentativa, o extrato é
    conferido antes de pedir o crédito de novo."""
    pendentes = (
        CobrancaPix.objects.filter(
            conta_recebimento_id=repasse.conta_recebimento_id,
            status=CobrancaPix.PAGA,
            creditado_em__isnull=True,
        )
        .select_related("conta_recebimento")
        .order_by("pago_em", "pk")
    )
    for cobranca in pendentes:
        liquido = cobranca.valor_liquido
        if liquido is None and cobranca.taxa is not None:
            liquido = max(Decimal("0"), cobranca.valor - cobranca.taxa)
        if liquido is None:
            raise TaxaDesconhecida(
                f"Pix {cobranca.correlation_id} pago sem a taxa da Woovi: informe a taxa na "
                "cobrança Pix (admin) e use 'Tentar o repasse de novo'."
            )
        centavos = int(liquido * 100)
        ja_creditado = cobranca.credito_incerto and _credito_no_extrato(client, cobranca, centavos)
        if centavos > 0 and not ja_creditado:
            try:
                client.creditar_subconta(
                    repasse.pix_key_destino, centavos, descricao=f"Pix {cobranca.correlation_id}",
                )
            except WooviTimeoutError:
                CobrancaPix.objects.filter(pk=cobranca.pk).update(credito_incerto=True)
                raise
        CobrancaPix.objects.filter(pk=cobranca.pk).update(
            valor_liquido=liquido, creditado_em=timezone.now(), credito_incerto=False,
        )


# ------------------------------------------------------------------ o job
def reivindicar(repasse_id, agora=None):
    """PENDENTE/FALHA vencido -> PROCESSANDO, atomicamente. Só um processo
    consegue; os demais recebem False."""
    agora = agora or timezone.now()
    return bool(
        Repasse.objects.filter(
            pk=repasse_id,
            status__in=[Repasse.PENDENTE, Repasse.FALHA],
            proxima_tentativa_em__lte=agora,
        ).update(status=Repasse.PROCESSANDO, tentativas=F("tentativas") + 1, atualizado_em=agora)
    )


def processar_repasse(repasse_id):
    """Uma tentativa de repasse. Devolve o repasse atualizado, ou None se
    outro processo já o pegou."""
    if not reivindicar(repasse_id):
        return None
    repasse = Repasse.objects.select_related("conta_recebimento").get(pk=repasse_id)

    try:
        client = WooviClient()

        if repasse.reconciliar:
            saque, estorno = _saque_no_extrato(client, repasse)
            if saque is not None and estorno is None:
                # O pedido que deu timeout saiu mesmo: aguarda a confirmação.
                repasse.reconciliar = False
                repasse.save()
                return repasse
            repasse.reconciliar = False
            repasse.save()

        _creditar_pagamentos(client, repasse)

        subconta = client.obter_subconta(repasse.pix_key_destino)
        if subconta.saque_bloqueado:
            ContaRecebimento.objects.filter(pk=repasse.conta_recebimento_id).update(saque_bloqueado=True)
            return _requer_atencao(repasse, "O provedor bloqueou transferências para esta chave Pix.")

        saldo = subconta.saldo_centavos
        valor = valor_do_saque(saldo)
        if valor < saque_minimo_centavos():
            if saldo <= 0 and repasse.tentativas < TENTATIVAS_AGUARDANDO_SALDO:
                return _aguardar_saldo(repasse)
            repasse.valor = Decimal("0")
            return _concluir(repasse, observacao=(
                "" if saldo <= 0 else
                "Saldo abaixo do mínimo para transferência (contando a tarifa); segue no próximo repasse."
            ))

        # Registra o que vai pedir ANTES da chamada: se ela terminar sem
        # resposta, é com isto que o extrato será conferido.
        repasse.valor = Decimal(valor) / 100
        repasse.processado_em = timezone.now()
        repasse.save()

        try:
            saque = client.sacar_subconta(repasse.pix_key_destino, valor)
        except WooviTimeoutError as exc:
            repasse.reconciliar = True
            return _falha(repasse, exc)

        repasse.correlation_id = saque.correlation_id or repasse.correlation_id
        repasse.end_to_end_id = saque.end_to_end_id or repasse.end_to_end_id
        if saque.status.upper() in STATUS_SAQUE_CONFIRMADO:
            return _concluir(repasse)
        repasse.save()  # segue PROCESSANDO até o webhook de confirmação
        return repasse

    except TaxaDesconhecida as exc:
        return _requer_atencao(repasse, exc)
    except WooviError as exc:
        return _falha(repasse, exc)


def conferir_repasse_em_andamento(repasse_id):
    """Repasse PROCESSANDO sem confirmação há muito tempo: decide pelo
    extrato da subconta."""
    repasse = Repasse.objects.select_related("conta_recebimento").get(pk=repasse_id)
    if repasse.status != Repasse.PROCESSANDO:
        return repasse
    try:
        saque, estorno = _saque_no_extrato(WooviClient(), repasse)
    except WooviError as exc:
        logger.warning("Não foi possível conferir o repasse %s no extrato: %s", repasse.pk, exc)
        return repasse
    if estorno is not None:
        return _falha(repasse, "O saque foi estornado pelo provedor.")
    if saque is not None:
        return _concluir(repasse)
    # Não aparece no extrato: o pedido não foi executado. Tentar de novo é
    # seguro — o próximo saque parte do saldo real, que ainda contém o valor.
    return _falha(repasse, "O saque não apareceu no extrato do provedor.")


def processar_repasses(limite=50):
    """Entrada do comando agendado. Devolve contagens para o log."""
    agora = timezone.now()
    devidos = list(
        Repasse.objects.filter(
            status__in=[Repasse.PENDENTE, Repasse.FALHA], proxima_tentativa_em__lte=agora,
        ).order_by("proxima_tentativa_em").values_list("pk", flat=True)[:limite]
    )
    processados = sum(1 for pk in devidos if processar_repasse(pk) is not None)

    parados = list(
        Repasse.objects.filter(
            status=Repasse.PROCESSANDO, processado_em__lte=agora - PRAZO_CONFIRMACAO,
        ).values_list("pk", flat=True)[:limite]
    )
    for pk in parados:
        conferir_repasse_em_andamento(pk)

    return {"processados": processados, "conferidos": len(parados)}


# --------------------------------------------------------------- webhooks
def _localizar(correlation_id, valor_centavos=None, destino="", end_to_end_id=""):
    repasse = Repasse.objects.filter(correlation_id=correlation_id).first() if correlation_id else None
    if repasse is None and end_to_end_id:
        # Em produção o webhook do saque veio com um correlationID diferente
        # do devolvido pelo /withdraw; o endToEndId é o mesmo nos dois.
        repasse = Repasse.objects.filter(end_to_end_id=end_to_end_id).first()
    if repasse is None and valor_centavos is not None and destino:
        # O webhook pode chegar antes de gravarmos o correlationID (ou o
        # pedido ter dado timeout): casa pelo destino e valor em andamento.
        repasse = Repasse.objects.filter(
            Q(status=Repasse.PROCESSANDO) | Q(status=Repasse.FALHA, reconciliar=True),
            pix_key_destino=destino,
            valor=Decimal(int(valor_centavos)) / 100,
        ).first()
    return repasse


def confirmar_repasse(correlation_id, end_to_end_id="", valor_centavos=None, destino=""):
    """OPENPIX:MOVEMENT_CONFIRMED. Idempotente."""
    repasse = _localizar(correlation_id, valor_centavos, destino, end_to_end_id)
    if repasse is None:
        return None
    if repasse.status != Repasse.CONCLUIDA:
        if not repasse.correlation_id:
            repasse.correlation_id = correlation_id
        _concluir(repasse, end_to_end_id)
    return repasse


def falhar_repasse(correlation_id, motivo, valor_centavos=None, destino="", end_to_end_id=""):
    """OPENPIX:MOVEMENT_FAILED: o Pix de saída não chegou; tenta de novo."""
    repasse = _localizar(correlation_id, valor_centavos, destino, end_to_end_id)
    if repasse is None:
        return None
    if repasse.status == Repasse.PROCESSANDO:
        _falha(repasse, motivo)
    return repasse
