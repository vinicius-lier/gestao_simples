"""Provedor Evolution API (real).

Implementa o contrato neutro de ``base.py`` usando ``EvolutionClient``.
Erros da Evolution (HTTP, timeout, configuração) são traduzidos para
``WhatsAppProviderError`` — sanitizados, sem vazar API key.

FASE 2A: o envio funciona, mas nada dispara automaticamente — a régua de
lembretes só usa isto quando ``LEMBRETES_ENVIAM_N8N``/provider da academia
for ligado numa fase futura.
"""
from integracoes.whatsapp.base import WhatsAppProvider, WhatsAppProviderError


def _extrair_message_id(resp):
    """id da mensagem na resposta do sendText da Evolution, ou ""."""
    if not isinstance(resp, dict):
        return ""
    chave = resp.get("key")
    if isinstance(chave, dict) and chave.get("id"):
        return str(chave["id"])
    for campo in ("id", "messageId", "messageID"):
        if resp.get(campo):
            return str(resp[campo])
    return ""


class EvolutionWhatsAppProvider(WhatsAppProvider):
    nome = "evolution"

    def __init__(self, config=None):
        self.config = config

    # ------------------------------------------------------------------ infra
    def _enviar_texto(self, telefone_bruto, texto):
        from integracoes.evolution.client import (
            EvolutionAPIError,
            EvolutionConfigError,
            client_para_config,
        )
        from integracoes.whatsapp import normalizar_telefone

        if self.config is None:
            raise WhatsAppProviderError(
                "Integração Evolution não configurada para esta academia."
            )

        telefone = normalizar_telefone(telefone_bruto)
        if not telefone:
            raise WhatsAppProviderError(
                "O responsável não possui número de WhatsApp cadastrado."
            )

        try:
            client = client_para_config(self.config)
            resp = client.enviar_texto(telefone, texto)
        except (EvolutionAPIError, EvolutionConfigError) as exc:
            raise WhatsAppProviderError(str(exc)) from None

        return {
            "provider": self.nome,
            "message_id": _extrair_message_id(resp),
            "raw": resp,
        }

    # --------------------------------------------------------------- contrato
    def enviar_cobranca(self, responsavel, mensalidade, link):
        texto = (
            f"Olá, {responsavel.nome}! A mensalidade de "
            f"{mensalidade.matricula.atleta.nome} referente a "
            f"{mensalidade.competencia:%m/%Y} está no valor de "
            f"R$ {mensalidade.valor}, com vencimento em "
            f"{mensalidade.vencimento:%d/%m/%Y}. Pague por aqui: {link}"
        )
        return self._enviar_texto(responsavel.whatsapp, texto)

    def enviar_acesso(self, responsavel, link):
        texto = (
            f"Olá, {responsavel.nome}! Aqui está o link para acompanhar as "
            f"mensalidades e pagar: {link}"
        )
        return self._enviar_texto(responsavel.whatsapp, texto)
