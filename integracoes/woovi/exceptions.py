"""Erros da integração Woovi.

Nenhuma mensagem carrega o AppID, headers ou a URL completa da requisição —
elas podem ir para log e, resumidas, para a tela da equipe.
"""


class WooviError(Exception):
    """Base de todos os erros da Woovi. ``status_code`` vem preenchido quando
    o erro nasceu de uma resposta HTTP."""

    def __init__(self, mensagem, status_code=None):
        super().__init__(mensagem)
        self.status_code = status_code


class WooviConfigError(WooviError):
    """WOOVI_APP_ID ou WOOVI_BASE_URL não configurados."""


class WooviAuthError(WooviError):
    """401/403: AppID inválido ou recurso não habilitado para a conta."""


class WooviNotFoundError(WooviError):
    """Recurso inexistente (404, ou 400 com "not found", como a Woovi às
    vezes responde)."""


class WooviRequestError(WooviError):
    """Demais 4xx: a Woovi recusou os dados enviados."""


class WooviUnavailableError(WooviError):
    """5xx ou falha de conexão. Pode tentar de novo."""


class WooviTimeoutError(WooviUnavailableError):
    """Tempo esgotado: a Woovi pode ter executado a operação ou não. Antes
    de repetir algo que não é idempotente (saque), reconcilie o estado."""


class WooviInvalidResponseError(WooviError):
    """Resposta fora do contrato documentado."""
