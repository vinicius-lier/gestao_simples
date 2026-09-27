"""Provedor Evolution API (real).

Implementa o contrato neutro de ``base.py`` usando ``EvolutionClient``.
Erros da Evolution (HTTP, timeout, configuração) são traduzidos para
``WhatsAppProviderError`` — sanitizados, sem vazar API key.

Cobrança e acesso ao portal vão como texto com o link. Com
``EVOLUTION_BOTOES`` ligado (desligado por padrão), vão como mensagem com
botão que abre a página de pagamento — mas o WhatsApp descartou essas
mensagens no teste real, ver config/settings.py. Se a Evolution recusar a
mensagem interativa, ela é reenviada como texto simples.
"""
import logging

from django.conf import settings

from integracoes.whatsapp.base import WhatsAppProvider, WhatsAppProviderError

logger = logging.getLogger(__name__)


def _extrair_message_id(resp):
    """id da mensagem na resposta do sendText/sendButtons da Evolution, ou ""."""
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
    def _cliente_e_telefone(self, telefone_bruto):
        from integracoes.evolution.client import EvolutionConfigError, client_para_config
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
            return client_para_config(self.config), telefone
        except EvolutionConfigError as exc:
            raise WhatsAppProviderError(str(exc)) from None

    def _resultado(self, resp):
        return {
            "provider": self.nome,
            "message_id": _extrair_message_id(resp),
            "raw": resp,
        }

    def _enviar_texto(self, telefone_bruto, texto):
        from integracoes.evolution.client import EvolutionAPIError

        client, telefone = self._cliente_e_telefone(telefone_bruto)
        try:
            resp = client.enviar_texto(telefone, texto)
        except EvolutionAPIError as exc:
            raise WhatsAppProviderError(str(exc)) from None
        return self._resultado(resp)

    def _enviar_com_botao(self, telefone_bruto, *, titulo, descricao, rotulo, link, texto_simples):
        """Mensagem com um botão que abre ``link``. Com os botões desligados,
        ou se a Evolution recusar a mensagem interativa, manda
        ``texto_simples``."""
        from integracoes.evolution.client import EvolutionAPIError

        if not getattr(settings, "EVOLUTION_BOTOES", False):
            return self._enviar_texto(telefone_bruto, texto_simples)

        client, telefone = self._cliente_e_telefone(telefone_bruto)
        academia = getattr(self.config, "academia", None)
        rodape = (academia.nome_fantasia or academia.nome) if academia else ""
        try:
            resp = client.enviar_botoes(
                telefone,
                titulo,
                descricao,
                [{"type": "url", "displayText": rotulo, "url": link}],
                rodape=rodape,
            )
        except EvolutionAPIError as exc:
            logger.warning("Evolution recusou a mensagem com botão (%s); enviando como texto.", exc)
            return self._enviar_texto(telefone_bruto, texto_simples)
        return self._resultado(resp)

    # --------------------------------------------------------------- contrato
    def enviar_cobranca(self, responsavel, mensalidade, link, estagio=None):
        aluno = mensalidade.matricula.atleta.nome
        campos = {
            "aluno": aluno,
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
        return self._enviar_com_botao(
            responsavel.whatsapp,
            titulo=f"Mensalidade de {aluno}",
            descricao=(
                f"Olá, {responsavel.nome}! {contexto}\n\n"
                f"Toque em *Pagar mensalidade* para pagar por Pix. "
                f"Se o botão não aparecer, use este link: {link}"
            ),
            rotulo="Pagar mensalidade",
            link=link,
            texto_simples=f"Olá, {responsavel.nome}! {contexto} Pague por aqui: {link}",
        )

    def enviar_acesso(self, responsavel, link):
        return self._enviar_com_botao(
            responsavel.whatsapp,
            titulo="Portal de pagamentos",
            descricao=(
                f"Olá, {responsavel.nome}! Acompanhe as mensalidades e pague por Pix.\n\n"
                f"Se o botão não aparecer, use este link: {link}"
            ),
            rotulo="Abrir portal",
            link=link,
            texto_simples=(
                f"Olá, {responsavel.nome}! Aqui está o link para acompanhar as "
                f"mensalidades e pagar: {link}"
            ),
        )

    def enviar_convite_matricula(self, nome, telefone, link, contexto=""):
        saudacao = f"Olá, {nome}!" if nome else "Olá!"
        complemento = f" para {contexto}" if contexto else ""
        texto = (
            f"{saudacao} Aqui está o link para preencher a matrícula"
            f"{complemento}: {link}"
        )
        return self._enviar_texto(telefone, texto)

    def enviar_aviso(self, telefone, texto):
        return self._enviar_texto(telefone, texto)
