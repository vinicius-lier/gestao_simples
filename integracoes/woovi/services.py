"""Cobrança Pix das mensalidades via Woovi.

Cada mensalidade em aberto tem no máximo um Pix vigente. O Pix vale
``VALIDADE_COBRANCA_DIAS``; quando expira, o próximo acesso gera outro,
com um correlationID novo (a Woovi exige correlationID único por cobrança).
"""
import logging
import re
import uuid
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from financeiro.models import Mensalidade
from integracoes.woovi.client import WooviAPIError, WooviClient

logger = logging.getLogger(__name__)

VALIDADE_COBRANCA_DIAS = 30

# Pix que expira dentro desta janela já é trocado por um novo, para a
# família não receber um código que morre antes de concluir o pagamento.
MARGEM_RENOVACAO = timedelta(hours=1)

STATUS_EM_ABERTO = ("pendente", "vencida")

CAMPOS_COBRANCA = [
    "woovi_correlation_id",
    "woovi_br_code",
    "woovi_qrcode_url",
    "woovi_link_pagamento",
    "woovi_expira_em",
]


def valor_em_centavos(valor):
    return int((Decimal(valor) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _precisa_de_novo_pix(mensalidade):
    return not (
        mensalidade.woovi_correlation_id
        and mensalidade.woovi_expira_em
        and mensalidade.woovi_expira_em > timezone.now() + MARGEM_RENOVACAO
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


def _aplicar_cobranca(mensalidade, cobranca):
    correlation_id = cobranca.get("correlationID")
    br_code = cobranca.get("brCode")
    if not correlation_id or not br_code:
        raise WooviAPIError("A resposta da Woovi não contém o Pix da cobrança.")

    mensalidade.woovi_correlation_id = correlation_id
    mensalidade.woovi_br_code = br_code
    mensalidade.woovi_qrcode_url = cobranca.get("qrCodeImage") or ""
    mensalidade.woovi_link_pagamento = cobranca.get("paymentLinkUrl") or ""
    mensalidade.woovi_expira_em = (
        parse_datetime(cobranca.get("expiresDate") or "")
        or timezone.now() + timedelta(days=VALIDADE_COBRANCA_DIAS)
    )


def _validar_em_aberto(mensalidade):
    if mensalidade.status not in STATUS_EM_ABERTO:
        raise ValueError(
            f"Uma mensalidade {mensalidade.get_status_display().lower()} "
            "não pode ser cobrada."
        )


def garantir_cobranca_pix(mensalidade):
    """Garante um Pix vigente para a mensalidade em aberto (pendente ou
    vencida) e devolve a própria mensalidade com os campos preenchidos.
    Idempotente: se já existe Pix vigente, não chama a Woovi.

    O lock na linha impede que o operador e a família, clicando ao mesmo
    tempo, gerem dois Pix para a mesma mensalidade."""
    _validar_em_aberto(mensalidade)
    if not _precisa_de_novo_pix(mensalidade):
        return mensalidade

    with transaction.atomic():
        travada = (
            Mensalidade.objects.select_for_update()
            .select_related("matricula__atleta__responsavel_financeiro")
            .get(pk=mensalidade.pk)
        )
        _validar_em_aberto(travada)

        if _precisa_de_novo_pix(travada):
            aluno = travada.matricula.atleta
            cobranca = WooviClient().criar_cobranca(
                correlation_id=f"mensalidade-{travada.pk}-{uuid.uuid4().hex}",
                valor_centavos=valor_em_centavos(travada.valor),
                # Vai para o infoPagador do Pix (máx. 140 caracteres).
                comentario=f"Mensalidade {travada.competencia:%m/%Y} - {aluno.nome}"[:140],
                expira_em_segundos=VALIDADE_COBRANCA_DIAS * 24 * 60 * 60,
                cliente=_dados_cliente(aluno.responsavel_financeiro),
            )
            _aplicar_cobranca(travada, cobranca)
            travada.save(update_fields=CAMPOS_COBRANCA)

    for campo in CAMPOS_COBRANCA:
        setattr(mensalidade, campo, getattr(travada, campo))
    return mensalidade


def remover_cobranca_pix(mensalidade):
    """Exclui na Woovi o Pix vigente da mensalidade, para ele parar de
    aceitar pagamento. Pix já expirado não aceita pagamento — nada a fazer.
    Falha (ex.: o Pix acabou de ser pago) levanta WooviAPIError."""
    if not mensalidade.pix_vigente:
        return
    WooviClient().remover_cobranca(mensalidade.woovi_correlation_id)


def confirmar_pagamento_pix(correlation_id):
    """Dá baixa na mensalidade se a própria API da Woovi confirmar que o Pix
    foi pago. O webhook só dispara esta conferência: a fonte da verdade é a
    consulta autenticada com o nosso AppID, então um POST forjado no webhook
    não consegue marcar nada como pago.

    Devolve a mensalidade baixada, ou None se não havia o que baixar."""
    from financeiro.services import registrar_pagamento

    mensalidade = (
        Mensalidade.objects.filter(woovi_correlation_id=correlation_id)
        .select_related("matricula__atleta")
        .first()
    )
    if mensalidade is None or mensalidade.status == "paga":
        return None

    cobranca = WooviClient().obter_cobranca(correlation_id)
    if cobranca.get("status") != "COMPLETED":
        return None

    if mensalidade.status == "cancelada":
        logger.warning(
            "Woovi: Pix %s pago para a mensalidade %s, que está cancelada. "
            "Conferir e devolver/regularizar manualmente.",
            correlation_id,
            mensalidade.pk,
        )
        return None

    registrar_pagamento(
        mensalidade,
        forma_pagamento="pix",
        quando=parse_datetime(cobranca.get("paidAt") or ""),
    )
    return mensalidade
