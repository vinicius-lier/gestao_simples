"""Contrato Django -> n8n.

Nesta fase o pacote existe para (1) montar o payload de cobrança que o n8n
receberá e (2) oferecer um cliente HTTP pronto. O client NÃO é acionado pelo
fluxo de lembretes ainda — só entra quando a automação n8n + Evolution
estiver de pé (FASE 2).
"""
from integracoes.n8n.client import N8nAPIError, N8nClient
from integracoes.n8n.services import montar_payload_cobranca

__all__ = ["N8nClient", "N8nAPIError", "montar_payload_cobranca"]
