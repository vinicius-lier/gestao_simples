"""Credenciais só no servidor. Não há fallback academia -> plataforma."""
import hashlib
import os
import re

from django.conf import settings
from django.views.decorators.debug import sensitive_variables

from .exceptions import WooviConfigError


def referencia_academia(academia_id):
    return f"WOOVI_ACADEMIA_{academia_id}_APP_ID"


@sensitive_variables()
def resolver(referencia):
    if not re.fullmatch(r"WOOVI_(?:APP_ID|PLATAFORMA_APP_ID|ONBOARDING_APP_ID|ACADEMIA_\d+_APP_ID)", referencia or ""):
        raise WooviConfigError("Referência de credencial inválida.")
    segredo = getattr(settings, referencia, None)
    if segredo is None:
        segredo = os.getenv(referencia, "")
    if not segredo:
        raise WooviConfigError("Credencial de integração não configurada no servidor.")
    return segredo


@sensitive_variables()
def fingerprint(segredo):
    return hashlib.sha256(segredo.encode()).hexdigest()


def referencia_plataforma():
    # O AppID original permanece reservado à conta antiga. Não o substitua
    # pelo AppID da academia. A referência é gravada em cada fatura.
    return "WOOVI_PLATAFORMA_APP_ID" if getattr(settings, "WOOVI_PLATAFORMA_APP_ID", "") else "WOOVI_APP_ID"


@sensitive_variables()
def da_conta(conta):
    if conta.legada:
        if conta.credencial_ref not in ("WOOVI_APP_ID", "WOOVI_PLATAFORMA_APP_ID"):
            raise WooviConfigError("A conta legada não pode usar credencial da academia.")
    elif conta.credencial_ref != referencia_academia(conta.academia_id):
        raise WooviConfigError("Credencial incompatível com a academia.")
    segredo = resolver(conta.credencial_ref)
    if not conta.legada:
        reservados = [getattr(settings, nome, "") for nome in (
            "WOOVI_APP_ID", "WOOVI_PLATAFORMA_APP_ID", "WOOVI_ONBOARDING_APP_ID",
        )]
        if segredo in reservados:
            raise WooviConfigError("A academia deve usar uma credencial exclusiva.")
    if conta.credencial_fingerprint and fingerprint(segredo) != conta.credencial_fingerprint:
        raise WooviConfigError("A credencial da conta mudou. Solicite a verificação da integração.")
    return segredo
