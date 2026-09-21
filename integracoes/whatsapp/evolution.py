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


_CONTEXTO_ESTAGIO = {
    "5_dias": "Faltam 5 dias para o vencimento da mensalidade de {aluno} ({competencia}), "
              "no valor de R$ {valor}, em {vencimento}.",
    "1_dia": "A mensalidade de {aluno} ({competencia}), no valor de R$ {valor}, "
             "vence amanhã ({vencimento}).",
    "vencimento": "A mensalidade de {aluno} ({competencia}), no valor de R$ {valor}, "
                  "vence hoje ({vencimento}).",
    "atrasada": "A mensalidade de {aluno} ({competencia}), no valor de R$ {valor}, "
                "venceu em {vencimento} e está em aberto.",
}


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
    def enviar_cobranca(self, responsavel, mensalidade, link, estagio=None):
        campos = {
            "aluno": mensalidade.matricula.atleta.nome,
            "competencia": f"{mensalidade.competencia:%m/%Y}",
            "valor": mensalidade.valor,
            "vencimento": f"{mensalidade.vencimento:%d/%m/%Y}",
        }
        molde = _CONTEXTO_ESTAGIO.get(estagio)
        if molde is None:
            molde = (
                "A mensalidade de {aluno} referente a {competencia} está no "
                "valor de R$ {valor}, com vencimento em {vencimento}."
            )
        contexto = molde.format(**campos)
        texto = f"Olá, {responsavel.nome}! {contexto} Pague por aqui: {link}"
        return self._enviar_texto(responsavel.whatsapp, texto)

    def enviar_acesso(self, responsavel, link):
        texto = (
            f"Olá, {responsavel.nome}! Aqui está o link para acompanhar as "
            f"mensalidades e pagar: {link}"
        )
        return self._enviar_texto(responsavel.whatsapp, texto)

    def enviar_convite_matricula(self, nome, telefone, link, contexto=""):
        saudacao = f"Olá, {nome}!" if nome else "Olá!"
        complemento = f" para {contexto}" if contexto else ""
        texto = (
            f"{saudacao} Aqui está o link para preencher a matrícula"
            f"{complemento}: {link}"
        )
        return self._enviar_texto(telefone, texto)
