"""Recebimento das mensalidades por Pix via Woovi.

Fluxo: a academia cadastra a chave Pix (``configurar_chave_pix``), que
identifica uma subconta na Woovi. Cada Pix de mensalidade é criado SEM
split (``garantir_cobranca_pix``): o valor entra na conta principal. A Woovi
não aceita split de 100%, e o valor pago é todo da academia — então, quando o
Pix é pago, o webhook registra o pagamento com a taxa da Woovi e abre um
repasse (``registrar_pagamento_pix``); o job ``processar_repasses`` (ver
``repasses``) credita na subconta o valor líquido (pago menos a taxa, que é
paga pela academia) e transfere o saldo da subconta para a chave Pix.

Mensagens de erro de negócio (``ValueError`` e subclasses) podem ir para a
tela: não citam o provedor.
"""
import logging
import re
import uuid
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from financeiro.alertas import alertar_plataforma
from financeiro.models import CobrancaPix, ContaRecebimento, EventoWebhook, Mensalidade, Repasse
from integracoes.woovi.chave_pix import normalizar_chave_pix
from integracoes.woovi.client import WooviClient
from integracoes.woovi.exceptions import WooviError, WooviInvalidResponseError, WooviNotFoundError

logger = logging.getLogger(__name__)

VALIDADE_COBRANCA_DIAS = 30

# Pix que expira dentro desta janela já é trocado por um novo, para a
# família não receber um código que morre antes de concluir o pagamento.
MARGEM_RENOVACAO = timedelta(hours=1)

STATUS_EM_ABERTO = ("pendente", "vencida")

EVENTO_PIX_PAGO = "OPENPIX:CHARGE_COMPLETED"
EVENTO_SAQUE_CONFIRMADO = "OPENPIX:MOVEMENT_CONFIRMED"
EVENTO_SAQUE_FALHOU = "OPENPIX:MOVEMENT_FAILED"


class RecebimentoNaoConfigurado(ValueError):
    """A academia ainda não cadastrou a chave Pix de recebimento."""


class RecebimentoBloqueado(ValueError):
    """Há transferência em andamento; a chave não pode ser trocada agora."""


def valor_em_centavos(valor):
    return int((Decimal(valor) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def nome_da_academia(academia):
    return academia.nome_fantasia or academia.nome



# ------------------------------------------------------ conta de recebimento
def configurar_chave_pix(academia, tipo_chave, valor, usuario=None):
    """Cadastra ou troca a chave Pix onde a academia recebe.

    A chave identifica a subconta no provedor e nunca é editada. Trocar:
      1. só é permitido sem repasse em andamento;
      2. cria/recupera a subconta da nova chave ANTES de mudar o banco — se
         o provedor recusar, nada muda;
      3. cancela os Pix ainda não pagos da conta antiga (serão gerados de
         novo, já para a nova chave, no próximo acesso);
      4. desativa a conta antiga (histórico preservado) e ativa a nova.
    """
    pix_key = normalizar_chave_pix(tipo_chave, valor)
    atual = ContaRecebimento.ativa_da(academia)
    if atual is not None and atual.pix_key == pix_key:
        return atual

    if atual is not None:
        _exigir_sem_repasse_aberto(atual)

    subconta = WooviClient().criar_ou_obter_subconta(pix_key, nome_da_academia(academia))

    if atual is not None:
        _cancelar_pix_em_aberto(atual)

    with transaction.atomic():
        if atual is not None:
            atual = ContaRecebimento.objects.select_for_update().get(pk=atual.pk)
            _exigir_sem_repasse_aberto(atual)
            atual.ativa = False
            atual.desativada_em = timezone.now()
            atual.desativada_por = usuario
            atual.save(update_fields=["ativa", "desativada_em", "desativada_por"])

        nova = ContaRecebimento.objects.create(
            academia=academia,
            tipo_chave=tipo_chave,
            pix_key=pix_key,
            saque_bloqueado=subconta.saque_bloqueado,
            criada_por=usuario,
        )

    logger.info(
        "Chave Pix de recebimento da academia %s %s (conta %s).",
        academia.pk, "trocada" if atual else "cadastrada", nova.pk,
    )
    if subconta.saque_bloqueado:
        alertar_plataforma(
            "Chave Pix cadastrada com transferência bloqueada pelo provedor",
            academia=academia.pk, conta=nova.pk,
        )
    return nova


def _exigir_sem_repasse_aberto(conta):
    if Repasse.objects.filter(conta_recebimento=conta, status__in=Repasse.STATUS_ABERTOS).exists():
        raise RecebimentoBloqueado(
            "Há uma transferência de valores em andamento para a chave atual. "
            "Tente trocar a chave novamente em alguns minutos."
        )


def _cancelar_pix_em_aberto(conta):
    """Tira do ar os Pix ativos da conta. Falha em algum (exceto "não
    encontrado") interrompe a troca de chave — nada é trocado pela metade."""
    client = WooviClient()
    for cobranca in CobrancaPix.objects.filter(conta_recebimento=conta, status=CobrancaPix.ATIVA):
        if cobranca.vigente:
            try:
                client.remover_cobranca(cobranca.correlation_id)
            except WooviNotFoundError:
                pass
            novo_status = CobrancaPix.CANCELADA
        else:
            novo_status = CobrancaPix.EXPIRADA
        CobrancaPix.objects.filter(pk=cobranca.pk, status=CobrancaPix.ATIVA).update(status=novo_status)


# ----------------------------------------------------------------- cobranças
def _validar_em_aberto(mensalidade):
    if mensalidade.status not in STATUS_EM_ABERTO:
        raise ValueError(
            f"Uma mensalidade {mensalidade.get_status_display().lower()} "
            "não pode ser cobrada."
        )


def _serve(cobranca, conta):
    return (
        cobranca is not None
        and cobranca.conta_recebimento_id == conta.pk
        and cobranca.expira_em > timezone.now() + MARGEM_RENOVACAO
    )


def _dados_cliente(responsavel):
    """Cliente da cobrança. A Woovi exige nome + (CPF, e-mail ou
    telefone); sem nenhum deles, a cobrança sai sem cliente."""
    from integracoes.whatsapp import normalizar_telefone

    if responsavel is None:
        return None

    cliente = {"name": responsavel.nome}
    cpf = re.sub(r"\D", "", responsavel.cpf or "")
    if cpf:
        cliente["taxID"] = cpf
    if responsavel.email:
        cliente["email"] = responsavel.email
    telefone = normalizar_telefone(responsavel.whatsapp)
    if telefone:
        cliente["phone"] = telefone

    return cliente if len(cliente) > 1 else None


def garantir_cobranca_pix(mensalidade):
    """Devolve um Pix (``CobrancaPix``) vigente para a mensalidade em aberto
    — pendente ou vencida —, criando um se preciso. Idempotente: com Pix
    vigente para a conta de recebimento ativa, não chama o provedor.

    O Pix é criado sem split: o valor entra na conta principal e só depois
    do pagamento o líquido é creditado na subconta da conta de recebimento
    (ver ``repasses``). A cobrança guarda a conta de recebimento vigente.

    O lock na linha da mensalidade impede que operador e família, clicando
    ao mesmo tempo, gerem dois Pix."""
    _validar_em_aberto(mensalidade)
    conta = ContaRecebimento.ativa_da(mensalidade.academia)
    if conta is None:
        raise RecebimentoNaoConfigurado(
            "A chave Pix de recebimento da academia ainda não foi cadastrada."
        )

    atual = CobrancaPix.objects.filter(mensalidade=mensalidade, status=CobrancaPix.ATIVA).first()
    if _serve(atual, conta):
        return atual

    with transaction.atomic():
        travada = (
            Mensalidade.objects.select_for_update()
            .select_related("matricula__atleta__responsavel_financeiro")
            .get(pk=mensalidade.pk)
        )
        _validar_em_aberto(travada)
        atual = (
            CobrancaPix.objects.select_for_update()
            .filter(mensalidade=travada, status=CobrancaPix.ATIVA)
            .first()
        )
        if _serve(atual, conta):
            return atual

        client = WooviClient()
        if atual is not None:
            _encerrar_cobranca_substituida(client, atual)

        valor_centavos = valor_em_centavos(travada.valor)
        aluno = travada.matricula.atleta
        criada = client.criar_cobranca(
            correlation_id=f"mensalidade-{travada.pk}-{uuid.uuid4().hex}",
            valor_centavos=valor_centavos,
            # Vai para o infoPagador do Pix (máx. 140 caracteres).
            comentario=f"Mensalidade {travada.competencia:%m/%Y} - {aluno.nome}"[:140],
            expira_em_segundos=VALIDADE_COBRANCA_DIAS * 24 * 60 * 60,
            cliente=_dados_cliente(aluno.responsavel_financeiro),
        )
        if not criada.correlation_id or not criada.br_code:
            raise WooviInvalidResponseError("A resposta da Woovi não contém o Pix da cobrança.")

        return CobrancaPix.objects.create(
            mensalidade=travada,
            conta_recebimento=conta,
            correlation_id=criada.correlation_id,
            transaction_id=criada.transaction_id,
            valor=travada.valor,
            br_code=criada.br_code,
            link_pagamento=criada.link_pagamento,
            expira_em=criada.expira_em or timezone.now() + timedelta(days=VALIDADE_COBRANCA_DIAS),
        )


def _encerrar_cobranca_substituida(client, cobranca):
    """O Pix anterior vai ser substituído (expirou, está perto de expirar ou
    é de uma chave antiga). Se ainda aceita pagamento, tenta tirá-lo do ar;
    se a família pagar por ele mesmo assim, o webhook registra o pagamento
    normalmente (ver ``registrar_pagamento_pix``)."""
    if cobranca.expira_em > timezone.now():
        try:
            client.remover_cobranca(cobranca.correlation_id)
        except WooviError as exc:  # melhor esforço; ver docstring
            logger.warning("Não foi possível cancelar o Pix %s: %s", cobranca.correlation_id, exc)
        cobranca.status = CobrancaPix.CANCELADA
    else:
        cobranca.status = CobrancaPix.EXPIRADA
    cobranca.save(update_fields=["status", "atualizada_em"])


def remover_cobranca_pix(mensalidade):
    """Tira do ar o Pix vigente da mensalidade (baixa manual, cancelamento,
    isenção). Pix já expirado não aceita pagamento — nada a fazer. Falha
    levanta WooviError."""
    cobranca = mensalidade.cobranca_pix_vigente
    if cobranca is None:
        return
    try:
        WooviClient().remover_cobranca(cobranca.correlation_id)
    except WooviNotFoundError:
        pass
    CobrancaPix.objects.filter(pk=cobranca.pk, status=CobrancaPix.ATIVA).update(
        status=CobrancaPix.CANCELADA, atualizada_em=timezone.now()
    )


def conferir_pagamento_pix(mensalidade):
    """Pergunta ao provedor se algum Pix ativo da mensalidade já foi pago
    (rede de segurança para webhook perdido). Dá baixa e abre o repasse se
    sim. Devolve True se registrou um pagamento."""
    client = None
    for cobranca in CobrancaPix.objects.filter(mensalidade=mensalidade, status=CobrancaPix.ATIVA):
        client = client or WooviClient()
        remota = client.obter_cobranca(cobranca.correlation_id)
        if remota.status == "COMPLETED":
            return registrar_pagamento_pix(
                cobranca, pago_em=remota.pago_em, transaction_id=remota.transaction_id,
                taxa_centavos=remota.taxa_centavos, valor_pago_centavos=remota.valor_centavos or None,
            )
        if remota.status == "EXPIRED":
            CobrancaPix.objects.filter(pk=cobranca.pk, status=CobrancaPix.ATIVA).update(
                status=CobrancaPix.EXPIRADA, atualizada_em=timezone.now()
            )
    return False


# ------------------------------------------------------ pagamento e repasse
def registrar_pagamento_pix(cobranca, *, pago_em=None, transaction_id="", taxa_centavos=None, valor_pago_centavos=None):
    """Registra o pagamento de um Pix: marca a cobrança e a mensalidade como
    pagas, guarda a taxa da Woovi e o líquido (o que será creditado na
    subconta — a taxa é paga pela academia) e abre um repasse para a conta
    que recebeu. Idempotente — o mesmo Pix pago duas vezes (webhook
    repetido) não gera nada de novo. Sem a taxa, o líquido fica vazio e o
    repasse pede atenção até alguém informá-la.

    O dinheiro caiu na subconta em qualquer caso, então o repasse é aberto
    mesmo quando a mensalidade já estava paga (pagamento em dobro) ou foi
    cancelada — esses casos também geram alerta para a plataforma
    resolver com a família. Devolve True se registrou agora."""
    from financeiro.services import registrar_pagamento

    with transaction.atomic():
        cobranca = (
            CobrancaPix.objects.select_for_update()
            .select_related("conta_recebimento__academia")
            .get(pk=cobranca.pk)
        )
        if cobranca.status == CobrancaPix.PAGA:
            return False

        cobranca.status = CobrancaPix.PAGA
        cobranca.pago_em = pago_em or timezone.now()
        cobranca.transaction_id = transaction_id or cobranca.transaction_id
        if taxa_centavos is not None:
            pago = int(valor_pago_centavos) if valor_pago_centavos else valor_em_centavos(cobranca.valor)
            cobranca.taxa = Decimal(int(taxa_centavos)) / 100
            cobranca.valor_liquido = Decimal(max(0, pago - int(taxa_centavos))) / 100
        cobranca.save(update_fields=[
            "status", "pago_em", "transaction_id", "taxa", "valor_liquido", "atualizada_em",
        ])

        mensalidade = Mensalidade.objects.select_for_update().get(pk=cobranca.mensalidade_id)
        if mensalidade.status == "paga":
            alertar_plataforma(
                "Mensalidade paga em dobro (Pix pago depois de outra baixa)",
                mensalidade=mensalidade.pk, cobranca=cobranca.correlation_id,
            )
        elif mensalidade.status == "cancelada":
            alertar_plataforma(
                "Pix pago de mensalidade cancelada — conferir e devolver/regularizar",
                mensalidade=mensalidade.pk, cobranca=cobranca.correlation_id,
            )
        else:
            registrar_pagamento(mensalidade, forma_pagamento="pix", quando=cobranca.pago_em)

        solicitar_repasse(cobranca.conta_recebimento)
    return True


def solicitar_repasse(conta):
    """Garante um repasse aberto para a conta. Se já houver um pendente ou
    aguardando nova tentativa, ele vai transferir o saldo todo — inclusive
    este pagamento. Se houver um em processamento (valor já definido),
    marca que chegou saldo novo, para outro repasse abrir quando ele
    terminar. Deve rodar dentro de uma transação."""
    aberto = (
        Repasse.objects.select_for_update()
        .filter(conta_recebimento=conta, status__in=Repasse.STATUS_ABERTOS)
        .first()
    )
    if aberto is None:
        try:
            with transaction.atomic():
                return Repasse.objects.create(
                    academia_id=conta.academia_id,
                    conta_recebimento=conta,
                    pix_key_destino=conta.pix_key,
                )
        except IntegrityError:
            # Outro pagamento abriu o repasse no mesmo instante.
            aberto = Repasse.objects.get(conta_recebimento=conta, status__in=Repasse.STATUS_ABERTOS)

    if aberto.status == Repasse.PROCESSANDO and not aberto.saldo_novo_pendente:
        aberto.saldo_novo_pendente = True
        aberto.save(update_fields=["saldo_novo_pendente", "atualizado_em"])
    return aberto


# ------------------------------------------------------------ webhooks
def dados_do_evento(payload):
    """(chave de idempotência, correlationID, resumo sem dados pessoais)
    para os eventos que o sistema trata, ou None para os demais — inclusive
    o POST de teste que a Woovi faz ao cadastrar o webhook."""
    evento = payload.get("event")

    def obj(nome):
        valor = payload.get(nome)
        return valor if isinstance(valor, dict) else {}

    if evento == EVENTO_PIX_PAGO:
        charge, pix = obj("charge"), obj("pix")
        correlation_id = charge.get("correlationID")
        if not correlation_id:
            return None
        referencia = pix.get("endToEndId") or charge.get("transactionID") or ""
        resumo = {
            "charge": {k: charge.get(k) for k in ("correlationID", "status", "value", "fee", "transactionID", "paidAt")},
            "pix": {k: pix.get(k) for k in ("endToEndId", "value", "time")},
        }
    elif evento in (EVENTO_SAQUE_CONFIRMADO, EVENTO_SAQUE_FALHOU):
        payment, transacao = obj("payment"), obj("transaction")
        correlation_id = payment.get("correlationID")
        if not correlation_id:
            return None
        referencia = transacao.get("endToEndId") or ""
        resumo = {
            "payment": {k: payment.get(k) for k in ("correlationID", "status", "value", "destinationAlias")},
            "transaction": {k: transacao.get(k) for k in ("endToEndId", "value", "time")},
            "error": obj("error"),
        }
    else:
        return None

    return f"{evento}:{correlation_id}:{referencia}"[:255], correlation_id, {"event": evento, **resumo}


def processar_evento(evento):
    """Aplica um ``EventoWebhook`` recém-gravado. Regras de negócio
    conhecidas (cobrança inexistente, já paga...) viram status IGNORADO com
    o motivo em ``erro`` — nunca exceção."""
    from integracoes.woovi import repasses

    dados = evento.payload
    motivo = ""
    if evento.tipo == EVENTO_PIX_PAGO:
        charge = dados.get("charge") or {}
        cobranca = CobrancaPix.objects.filter(correlation_id=evento.correlation_id).first()
        if cobranca is None:
            motivo = "cobrança não encontrada"
        elif not registrar_pagamento_pix(
            cobranca,
            pago_em=parse_datetime(charge.get("paidAt") or ""),
            transaction_id=charge.get("transactionID") or "",
            taxa_centavos=charge.get("fee"),
            valor_pago_centavos=charge.get("value"),
        ):
            motivo = "pagamento já registrado"
    else:
        payment = dados.get("payment") or {}
        transacao = dados.get("transaction") or {}
        erro = dados.get("error") or {}
        if evento.tipo == EVENTO_SAQUE_CONFIRMADO:
            repasse = repasses.confirmar_repasse(
                evento.correlation_id,
                end_to_end_id=transacao.get("endToEndId") or "",
                valor_centavos=payment.get("value"),
                destino=payment.get("destinationAlias") or "",
            )
        else:
            repasse = repasses.falhar_repasse(
                evento.correlation_id,
                motivo=erro.get("description") or erro.get("code") or "falha informada pelo provedor",
                valor_centavos=payment.get("value"),
                destino=payment.get("destinationAlias") or "",
                end_to_end_id=transacao.get("endToEndId") or "",
            )
        if repasse is None:
            motivo = "repasse não encontrado"

    evento.status = EventoWebhook.IGNORADO if motivo else EventoWebhook.PROCESSADO
    evento.erro = motivo
    evento.processado_em = timezone.now()
    evento.save(update_fields=["status", "erro", "processado_em"])
    return evento
