"""Credenciais Woovi em repouso: Fernet (AES-128-CBC + HMAC-SHA256).

A chave vem de ``CREDENTIALS_ENCRYPTION_KEY`` no servidor, nunca do código,
e é o mesmo nome usado pelo Ciclo. Só o AppID criado pela Partner API passa
por aqui; credenciais provisionadas pelo operador continuam em variáveis de
ambiente. Nenhuma função devolve o segredo em mensagem de erro.
"""
from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.views.decorators.debug import sensitive_variables

from .exceptions import WooviConfigError


@sensitive_variables()
def _fernet():
    chave = getattr(settings, "CREDENTIALS_ENCRYPTION_KEY", "")
    if not chave:
        raise WooviConfigError(
            "Configure CREDENTIALS_ENCRYPTION_KEY no servidor para guardar a credencial da conta."
        )
    try:
        return Fernet(chave.encode() if isinstance(chave, str) else chave)
    except (TypeError, ValueError):
        raise WooviConfigError(
            "CREDENTIALS_ENCRYPTION_KEY inválida: use uma chave Fernet (Fernet.generate_key())."
        ) from None


@sensitive_variables()
def cifrar(texto):
    """Texto cifrado, pronto para o banco. Vazio continua vazio."""
    if not texto:
        return ""
    return _fernet().encrypt(texto.encode()).decode()


@sensitive_variables()
def decifrar(cifrado):
    """Segredo em claro. Use só para montar a chamada HTTP; nunca registre."""
    if not cifrado:
        return ""
    try:
        return _fernet().decrypt(cifrado.encode()).decode()
    except InvalidToken:
        raise WooviConfigError(
            "A credencial guardada não abre com a chave atual. A CREDENTIALS_ENCRYPTION_KEY mudou?"
        ) from None
