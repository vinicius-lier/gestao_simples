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
    def enviar_cobranca(self, responsavel, mensalidade, link, estagio=None):
        """Envia o aviso de cobrança da mensalidade ao responsável.

        ``estagio`` é um dos valores de ``LembreteCobranca.ESTAGIOS``
        (``"5_dias"``, ``"1_dia"``, ``"vencimento"``, ``"atrasada"``) — usado
        pelos provedores que conseguem variar o texto pelo contexto (hoje,
        Evolution). ``None`` é um envio manual/avulso, fora da régua.

        Deve devolver um dict normalizado::

            {"provider": <str>, "message_id": <str>, "raw": <resposta bruta>}

        e levantar ``WhatsAppProviderError`` (ou subclasse) em falha."""

    @abstractmethod
    def enviar_acesso(self, responsavel, link):
        """Envia o link de acesso ao portal do responsável. Mesmo contrato
        de retorno/erro de ``enviar_cobranca``."""

    def enviar_convite_matricula(self, nome, telefone, link, contexto=""):
        """Envia o link de um convite de matrícula (texto livre) direto
        para a família, sem sair do app. Mesmo contrato de retorno/erro
        de ``enviar_cobranca``.

        Provedores baseados em templates pré-aprovados (Meta) não têm um
        template de convite de matrícula hoje — a implementação padrão
        recusa com ``WhatsAppProviderError`` e a tela cai no link manual.
        Só o Evolution (texto livre) sobrescreve isto."""
        raise WhatsAppProviderError(
            "Este provedor de WhatsApp não suporta o envio direto do convite de matrícula."
        )

    def enviar_aviso(self, telefone, texto):
        """Envia um aviso interno (texto livre) para a equipe da escola — hoje,
        a matrícula nova que precisa ser conferida e ativada. Mesmo contrato
        de retorno/erro de ``enviar_cobranca``. Como no convite, só o
        Evolution (texto livre) implementa; os demais recusam."""
        raise WhatsAppProviderError(
            "Este provedor de WhatsApp não suporta o envio de avisos para a escola."
        )
