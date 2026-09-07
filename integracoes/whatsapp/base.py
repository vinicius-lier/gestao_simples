"""Contrato neutro de envio por WhatsApp.

O restante do sistema (financeiro, portal) conversa só com esta interface e
com as funções neutras de ``integracoes.whatsapp`` — nunca com um provedor
concreto. Hoje existe o provedor Meta (Cloud API); o Evolution entra depois
sem que a regra de negócio precise mudar.
"""
from abc import ABC, abstractmethod


class WhatsAppProviderError(RuntimeError):
    """Erro previsível de qualquer provedor de WhatsApp.

    ``integracoes.whatsapp.meta.WhatsAppAPIError`` herda desta classe, então
    capturar ``WhatsAppProviderError`` cobre Meta e futuros provedores."""


class WhatsAppProvider(ABC):
    """Interface implementada por cada provedor concreto."""

    #: identificador curto do provedor, gravado em LembreteCobranca.provider
    nome = ""

    @abstractmethod
    def enviar_cobranca(self, responsavel, mensalidade, link):
        """Envia o aviso de cobrança da mensalidade ao responsável.

        Deve devolver um dict normalizado::

            {"provider": <str>, "message_id": <str>, "raw": <resposta bruta>}

        e levantar ``WhatsAppProviderError`` (ou subclasse) em falha."""

    @abstractmethod
    def enviar_acesso(self, responsavel, link):
        """Envia o link de acesso ao portal do responsável. Mesmo contrato
        de retorno/erro de ``enviar_cobranca``."""
