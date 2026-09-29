"""Verificação do header ``x-webhook-signature`` dos webhooks da Woovi.

A assinatura é base64(RSA-SHA256) sobre o corpo BRUTO da requisição, com a
chave privada da Woovi. As chaves públicas vêm de
GET /api/v1/webhook/public-keys, que pode listar mais de uma durante uma
rotação — qualquer uma delas vale.

Cache: as chaves ficam no cache do Django por ``WOOVI_WEBHOOK_CHAVES_TTL``.
Se nenhuma chave em cache validar a assinatura, a lista é buscada de novo
(a Woovi pode ter rotacionado), mas no máximo uma vez a cada
``INTERVALO_MINIMO_ATUALIZACAO`` — um POST forjado não consegue fazer o
servidor martelar a Woovi. Sem cache e sem acesso à Woovi, vale a chave
publicada na documentação.
"""
import base64
import binascii
import logging

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from django.conf import settings
from django.core.cache import cache

from integracoes.woovi.client import WooviClient
from integracoes.woovi.exceptions import WooviError

logger = logging.getLogger(__name__)

CHAVE_CACHE = "woovi:webhook:chaves-publicas"
CHAVE_CACHE_ATUALIZACAO = "woovi:webhook:chaves-publicas:atualizada"
INTERVALO_MINIMO_ATUALIZACAO = 60  # segundos

# Chave publicada em
# https://developers.woovi.com/docs/webhook/seguranca/webhook-signature-validation
CHAVE_DOCUMENTADA = """-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQC/+NtIkjzevvqD+I3MMv3bLXDt
pvxBjY4BsRrSdca3rtAwMcRYYvxSnd7jagVLpctMiOxQO8ieUCKLSWHpsMAjO/zZ
WMKbqoG8MNpi/u3fp6zz0mcHCOSqYsPUUG19buW8bis5ZZ2IZgBObWSpTvJ0cnj6
HKBAA82Jln+lGwS1MwIDAQAB
-----END PUBLIC KEY-----
"""


def _buscar_chaves(base_url=None):
    """Busca na Woovi e guarda no cache. Devolve None se não conseguir."""
    try:
        pems = WooviClient(contexto="publico", base_url=base_url).chaves_publicas_webhook()
    except WooviError as exc:
        logger.warning("Woovi: não foi possível atualizar as chaves do webhook: %s", exc)
        return None
    cache.set(_cache_key(CHAVE_CACHE, base_url), pems, getattr(settings, "WOOVI_WEBHOOK_CHAVES_TTL", 6 * 60 * 60))
    return pems


def _cache_key(chave, base_url):
    return f"{chave}:{base_url}" if base_url else chave


def _chaves(forcar=False, base_url=None):
    if not forcar:
        pems = cache.get(_cache_key(CHAVE_CACHE, base_url))
        if pems:
            return pems
    elif not cache.add(_cache_key(CHAVE_CACHE_ATUALIZACAO, base_url), True, INTERVALO_MINIMO_ATUALIZACAO):
        return None  # já atualizou há pouco
    return _buscar_chaves(base_url) or (None if forcar else [CHAVE_DOCUMENTADA])


def _confere(pems, corpo, assinatura):
    for pem in pems:
        try:
            chave = serialization.load_pem_public_key(pem.encode())
            chave.verify(assinatura, corpo, padding.PKCS1v15(), hashes.SHA256())
            return True
        except (InvalidSignature, ValueError, TypeError):
            continue
    return False


def assinatura_valida(corpo, assinatura_b64, base_url=None):
    """True se ``assinatura_b64`` (header x-webhook-signature) for uma
    assinatura válida da Woovi sobre ``corpo`` (bytes brutos)."""
    if not assinatura_b64:
        return False
    try:
        assinatura = base64.b64decode(assinatura_b64, validate=True)
    except (binascii.Error, ValueError):
        return False

    if _confere(_chaves(base_url=base_url), corpo, assinatura):
        return True
    # Pode ter havido rotação: busca a lista de novo (com limite de frequência).
    novas = _chaves(forcar=True, base_url=base_url)
    return bool(novas) and _confere(novas, corpo, assinatura)
