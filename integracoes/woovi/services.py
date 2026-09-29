"""Recebimento das mensalidades por Pix via Woovi.

Novas cobranças usam a conta própria da academia e terminam na baixa.
Cobranças antigas mantêm sua conta e credencial originais; somente elas
continuam no ciclo de crédito em subconta e repasse. A configuração antiga
de chaves permanece como ferramenta de manutenção legada, fora do portal.

Mensagens de erro de negócio (``ValueError`` e subclasses) podem ir para a
tela: não citam o provedor.
"""
import logging
import re
import uuid
from datetime import datetime, time, timedelta
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
    if atual is not None and not atual.legada:
        raise ValueError("Uma conta própria não pode ser substituída por uma chave legada.")
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
    client = WooviClient(conta=conta)
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


def _serve(cobranca, conta, valor):
    """O Pix atual ainda pode ser usado: mesma conta de recebimento, mesmo
    valor devido hoje (depois do vencimento o valor pode mudar) e longe de
    expirar."""
    return (
        cobranca is not None
        # A ativação da conta própria não invalida um Pix antigo já enviado.
        and (cobranca.modelo_recebimento == ContaRecebimento.LEGADO_SUBCONTA
             or (conta is not None and cobranca.conta_recebimento_id == conta.pk))
        and cobranca.valor == valor
        and cobranca.expira_em > timezone.now() + (
            timedelta(0) if cobranca.modelo_recebimento == ContaRecebimento.LEGADO_SUBCONTA else MARGEM_RENOVACAO
        )
    )


def _validade_em_segundos(mensalidade):
    """Quanto tempo o Pix aceita pagamento. Se o valor muda depois do
    vencimento, o Pix em dia expira no fim do dia do vencimento — depois
    sai um novo, com o valor maior. Com um mínimo de duas margens de
    renovação, para um Pix gerado perto da meia-noite não ser trocado a
    cada acesso."""
    padrao = VALIDADE_COBRANCA_DIAS * 24 * 60 * 60
    if not mensalidade.muda_apos_vencimento:
        return padrao
    fim_do_vencimento = timezone.make_aware(datetime.combine(mensalidade.vencimento, time.max))
    ate_o_vencimento = int((fim_do_vencimento - timezone.now()).total_seconds())
    minimo = int((2 * MARGEM_RENOVACAO).total_seconds())
    return max(minimo, min(padrao, ate_o_vencimento))



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

    Novos Pix usam exclusivamente a conta própria conectada da academia,
    sem split, crédito ou repasse. Códigos legados vigentes são preservados
    com sua conta de origem.

    O lock na linha da mensalidade impede que operador e família, clicando
    ao mesmo tempo, gerem dois Pix."""
    _validar_em_aberto(mensalidade)
    conta = ContaRecebimento.ativa_da(mensalidade.academia)
    atual = CobrancaPix.objects.filter(mensalidade=mensalidade, status=CobrancaPix.ATIVA).first()
    # Preserve códigos legados vigentes, inclusive seu valor original. Não
    # cancelar/reemitir automaticamente por mudança de conta ou de tarifa.
    if atual is not None and atual.modelo_recebimento == ContaRecebimento.LEGADO_SUBCONTA and atual.vigente:
        return atual
    if _serve(atual, conta, mensalidade.valor_devido()):
        return atual

    if conta is None or conta.legada or conta.status != ContaRecebimento.CONECTADA:
        raise RecebimentoNaoConfigurado(
            "Configure a conta de recebimento da academia antes de gerar novos Pix."
        )

    with transaction.atomic():
        travada = (
            Mensalidade.objects.select_for_update()
            .select_related("matricula__atleta__responsavel_financeiro")
            .get(pk=mensalidade.pk)
        )
        _validar_em_aberto(travada)
        conta = ContaRecebimento.objects.select_for_update().get(pk=conta.pk)
        if not conta.ativa or conta.legada or conta.status != ContaRecebimento.CONECTADA:
            raise RecebimentoNaoConfigurado("A conta de recebimento precisa estar conectada.")
        valor = travada.valor_devido()
        atual = (
            CobrancaPix.objects.select_for_update()
            .filter(mensalidade=travada, status=CobrancaPix.ATIVA)
            .first()
        )
        if _serve(atual, conta, valor):
            return atual

        if atual is not None and atual.modelo_recebimento == ContaRecebimento.LEGADO_SUBCONTA and atual.vigente:
            return atual
        client = WooviClient(conta=conta)
        if atual is not None:
            _encerrar_cobranca_substituida(WooviClient(conta=atual.conta_recebimento), atual)

        validade = _validade_em_segundos(travada)
        aluno = travada.matricula.atleta
        criada = client.criar_cobranca(
            correlation_id=f"mensalidade-{travada.pk}-{uuid.uuid4().hex}",
            valor_centavos=valor_em_centavos(valor),
            # Vai para o infoPagador do Pix (máx. 140 caracteres).
            comentario=f"{travada.descricao} - {aluno.nome}"[:140],
            expira_em_segundos=validade,
            cliente=_dados_cliente(aluno.responsavel_financeiro),
        )
        if not criada.correlation_id or not criada.br_code:
            raise WooviInvalidResponseError("A resposta da Woovi não contém o Pix da cobrança.")

        return CobrancaPix.objects.create(
            mensalidade=travada,
            conta_recebimento=conta,
            correlation_id=criada.correlation_id,
            transaction_id=criada.transaction_id,
            valor=valor,
            br_code=criada.br_code,
            link_pagamento=criada.link_pagamento,
            expira_em=criada.expira_em or timezone.now() + timedelta(seconds=validade),
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
        WooviClient(conta=cobranca.conta_recebimento).remover_cobranca(cobranca.correlation_id)
    except WooviNotFoundError:
        pass
    CobrancaPix.objects.filter(pk=cobranca.pk, status=CobrancaPix.ATIVA).update(
        status=CobrancaPix.CANCELADA, atualizada_em=timezone.now()
    )


def conferir_pagamento_pix(mensalidade):
    """Pergunta ao provedor se algum Pix ativo da mensalidade já foi pago
    (rede de segurança para webhook perdido). Dá baixa e abre o repasse se
    sim. Devolve True se registrou um pagamento."""
    for cobranca in CobrancaPix.objects.filter(mensalidade=mensalidade, status=CobrancaPix.ATIVA):
        client = WooviClient(conta=cobranca.conta_recebimento)
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
    """Baixa idempotente com valor pago, data e taxa real da Woovi.

    Conta própria encerra o fluxo na baixa. Somente cobranças legadas
    abrem repasse para crédito e transferência do líquido. Sem fee, taxa
    e líquido permanecem desconhecidos. Pagamentos duplicados ou de uma
    mensalidade cancelada geram alerta para conferência operacional.
    Devolve True se registrou agora.
    """
    from financeiro.services import registrar_pagamento

    with transaction.atomic():
        cobranca = (
            CobrancaPix.objects.select_for_update()
            .select_related("conta_recebimento__academia")
            .get(pk=cobranca.pk)
        )
        ja_paga = cobranca.status == CobrancaPix.PAGA
        # Uma consulta de contingência pode chegar sem fee. Um webhook
        # posterior completa os dados sem repetir a baixa ou o repasse.
        if ja_paga and (cobranca.taxa is not None or taxa_centavos is None):
            return False

        cobranca.status = CobrancaPix.PAGA
        cobranca.pago_em = cobranca.pago_em or pago_em or timezone.now()
        cobranca.transaction_id = transaction_id or cobranca.transaction_id
        if not ja_paga:
            cobranca.valor_pago = Decimal(int(valor_pago_centavos)) / 100 if valor_pago_centavos is not None else cobranca.valor
        if taxa_centavos is not None:
            pago = valor_em_centavos(cobranca.valor_pago if cobranca.valor_pago is not None else cobranca.valor)
            cobranca.taxa = Decimal(int(taxa_centavos)) / 100
            cobranca.valor_liquido = Decimal(max(0, pago - int(taxa_centavos))) / 100
        cobranca.save(update_fields=[
            "status", "pago_em", "transaction_id", "taxa", "valor_liquido", "valor_pago", "atualizada_em",
        ])
        if ja_paga:
            return False

        mensalidade = Mensalidade.objects.select_for_update().get(pk=cobranca.mensalidade_id)
        if mensalidade.status == "paga":
            alertar_plataforma(
                "Mensalidade paga em dobro (Pix pago depois de outra baixa)",
                mensalidade=mensalidade.pk, cobranca=cobranca.correlation_id,
            )
        elif mensalidade.status in ("cancelada", "isenta"):
            alertar_plataforma(
                f"Pix pago de mensalidade {mensalidade.status} — conferir e devolver/regularizar",
                mensalidade=mensalidade.pk, cobranca=cobranca.correlation_id,
            )
        else:
            valor_pago = (
                Decimal(int(valor_pago_centavos)) / 100 if valor_pago_centavos is not None else cobranca.valor
            )
            registrar_pagamento(
                mensalidade, forma_pagamento="pix", quando=cobranca.pago_em, valor_pago=valor_pago,
            )

        if cobranca.modelo_recebimento == ContaRecebimento.LEGADO_SUBCONTA:
            solicitar_repasse(cobranca.conta_recebimento)
    return True


def solicitar_repasse(conta):
    """Garante um repasse aberto para a conta. Se já houver um pendente ou
    aguardando nova tentativa, ele vai transferir o saldo todo — inclusive
    este pagamento. Se houver um em processamento (valor já definido),
    marca que chegou saldo novo, para outro repasse abrir quando ele
    terminar. Deve rodar dentro de uma transação; quando ela for gravada, o
    repasse é processado em segundo plano."""
    from integracoes.woovi.repasses import acompanhar_repasses

    if not conta.legada:
        raise ValueError("Contas próprias não possuem repasse pelo sistema.")
    transaction.on_commit(acompanhar_repasses)
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
    if evento.tipo == EVENTO_PIX_PAGO and evento.correlation_id.startswith("assinatura-"):
        # Mensalidade do sistema paga pela academia à plataforma: fica na
        # conta da plataforma (sem repasse) e reativa o painel suspenso.
        from assinaturas.services import fatura_do_correlation_id, registrar_pagamento_pix as pagar_assinatura

        charge = dados.get("charge") or {}
        if fatura_do_correlation_id(evento.correlation_id) is None:
            motivo = "fatura da assinatura não encontrada"
        elif not pagar_assinatura(
            evento.correlation_id,
            pago_em=parse_datetime(charge.get("paidAt") or ""),
            charge_id=charge.get("transactionID") or "",
        ):
            motivo = "pagamento já registrado"
    elif evento.tipo == EVENTO_PIX_PAGO:
        charge = dados.get("charge") or {}
        pix = dados.get("pix") or {}
        cobranca = CobrancaPix.objects.filter(correlation_id=evento.correlation_id).first()
        if cobranca is None:
            motivo = "cobrança não encontrada"
        elif not registrar_pagamento_pix(
            cobranca,
            pago_em=parse_datetime(charge.get("paidAt") or pix.get("time") or ""),
            transaction_id=charge.get("transactionID") or "",
            taxa_centavos=charge.get("fee"),
            valor_pago_centavos=pix.get("value") if pix.get("value") is not None else charge.get("value"),
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
        else:
            # A falha agenda nova tentativa (e a confirmação pode abrir outro
            # repasse): garante que alguém está acompanhando.
            transaction.on_commit(repasses.acompanhar_repasses)

    evento.status = EventoWebhook.IGNORADO if motivo else EventoWebhook.PROCESSADO
    evento.erro = motivo
    evento.processado_em = timezone.now()
    evento.save(update_fields=["status", "erro", "processado_em"])
    return evento
