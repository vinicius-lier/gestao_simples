"""Integração com a Evolution API (WhatsApp não-oficial, por instância).

FASE 2A: client HTTP real + serviços de gestão de instância. O disparo
automático de cobrança (n8n / régua) continua desligado.
"""
from integracoes.evolution.client import (
    EvolutionAPIError,
    EvolutionClient,
    EvolutionConfigError,
    client_para_config,
    resolver_credencial,
)

__all__ = [
    "EvolutionClient",
    "EvolutionAPIError",
    "EvolutionConfigError",
    "client_para_config",
    "resolver_credencial",
]
