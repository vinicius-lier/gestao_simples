"""Camada neutra de WhatsApp.

O financeiro e o portal chamam ``enviar_cobranca`` / ``enviar_acesso`` daqui e
não sabem (nem devem saber) qual provedor está configurado para a academia.
O provedor é resolvido por ``get_provider(academia)`` a partir de
``academias.IntegracaoWhatsApp`` (padrão: Meta).
"""
from integracoes.whatsapp.base import WhatsAppProvider, WhatsAppProviderError

__all__ = [
    "get_provider",
    "enviar_cobranca",
    "enviar_acesso",
    "enviar_convite_matricula",
    "normalizar_telefone",
    "WhatsAppProvider",
    "WhatsAppProviderError",
]


def normalizar_telefone(numero):
    """Reexport de conveniência — implementação em ``.services``."""
    from integracoes.whatsapp.services import normalizar_telefone as _impl

    return _impl(numero)


def get_provider(academia):
    """Devolve o provedor de WhatsApp da academia (Meta por padrão)."""
    from academias.models import IntegracaoWhatsApp
    from integracoes.whatsapp.evolution import EvolutionWhatsAppProvider
    from integracoes.whatsapp.meta import MetaWhatsAppProvider

    config = None
    if academia is not None:
        config = IntegracaoWhatsApp.objects.filter(academia=academia).first()

    provider = config.provider if config is not None else IntegracaoWhatsApp.PROVIDER_META

    if provider == IntegracaoWhatsApp.PROVIDER_EVOLUTION:
        return EvolutionWhatsAppProvider(config)
    return MetaWhatsAppProvider(config)


def enviar_cobranca(academia, responsavel, mensalidade, link, estagio=None):
    """Ponto único de envio do aviso de cobrança. Retorna o dict normalizado
    do provedor (`provider`, `message_id`, `raw`) e levanta
    ``WhatsAppProviderError`` em falha.

    ``estagio`` (opcional) identifica o estágio da régua de lembretes —
    ver ``financeiro.models.LembreteCobranca.ESTAGIOS`` — para provedores
    que variam o texto pelo contexto (Evolution). ``None`` para um envio
    manual/avulso."""
    return get_provider(academia).enviar_cobranca(
        responsavel, mensalidade, link, estagio=estagio
    )


def enviar_acesso(academia, responsavel, link):
    """Ponto único de envio do link de acesso ao portal do responsável."""
    return get_provider(academia).enviar_acesso(responsavel, link)


def enviar_convite_matricula(academia, nome, telefone, link, contexto=""):
    """Envia o link de um convite de matrícula direto pelo WhatsApp, sem
    sair do app. Hoje só o provedor Evolution suporta texto livre —
    levanta ``WhatsAppProviderError`` para provedores baseados em
    template (Meta), e a tela chamadora cai no link manual."""
    return get_provider(academia).enviar_convite_matricula(nome, telefone, link, contexto)
