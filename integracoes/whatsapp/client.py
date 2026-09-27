"""Retrocompatibilidade.

O cliente Meta foi movido para ``integracoes.whatsapp.meta``. Este módulo é
mantido só para não quebrar imports antigos
(``from integracoes.whatsapp.client import WhatsAppClient, WhatsAppAPIError``)
e patches de teste que apontam para ``integracoes.whatsapp.client.requests``.
"""
import requests  # noqa: F401  (mantém `integracoes.whatsapp.client.requests` para testes)

from integracoes.whatsapp.meta import WhatsAppAPIError, WhatsAppClient  # noqa: F401

__all__ = ["WhatsAppClient", "WhatsAppAPIError", "requests"]
