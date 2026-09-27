"""Camada HTTP da Evolution API.

Toda chamada à Evolution passa por aqui — as views/serviços nunca falam
`requests` diretamente. A API key:
  * é resolvida a partir de ``IntegracaoWhatsApp.credencial_ref`` (nome de uma
    variável de ambiente), nunca lida do banco;
  * nunca aparece em log, exceção ou valor de retorno (ver ``_sanitizar``).
"""
import os

import requests
from django.conf import settings


class EvolutionAPIError(RuntimeError):
    """Falha previsível e sanitizada ao comunicar com a Evolution API."""


class EvolutionConfigError(RuntimeError):
    """Configuração da integração ausente ou incompleta."""


def resolver_credencial(config):
    """Devolve a API key da Evolution para esta academia.

    ``config.credencial_ref`` guarda o *nome* de uma variável de ambiente
    (ex.: ``EVOLUTION_API_KEY_KEIKO``); o valor vem de ``os.getenv`` e nunca
    é persistido. Erros são de configuração, previsíveis."""
    ref = (getattr(config, "credencial_ref", "") or "").strip()
    if not ref:
        raise EvolutionConfigError(
            "Defina 'credencial_ref' na integração de WhatsApp da academia "
            "(nome da variável de ambiente que guarda a API key da Evolution)."
        )
    valor = os.getenv(ref)
    if not valor:
        raise EvolutionConfigError(
            f"A variável de ambiente '{ref}' não está definida no servidor."
        )
    return valor


class EvolutionClient:
    def __init__(self, base_url, instance_name, api_key, timeout=None):
        if not base_url:
            raise EvolutionConfigError("Evolution: URL base não configurada.")
        if not instance_name:
            raise EvolutionConfigError("Evolution: nome da instância não configurado.")
        if not api_key:
            raise EvolutionConfigError("Evolution: credencial não resolvida.")

        self.base_url = str(base_url).rstrip("/")
        self.instance_name = instance_name
        self._api_key = api_key
        self.timeout = timeout or getattr(settings, "EVOLUTION_TIMEOUT", 15)
        self.headers = {"apikey": api_key, "Content-Type": "application/json"}

    # ------------------------------------------------------------------ infra
    def _sanitizar(self, texto):
        """Remove API key e URL base de qualquer string antes de expô-la."""
        texto = str(texto)
        if self._api_key and self._api_key in texto:
            texto = texto.replace(self._api_key, "[apikey]")
        if self.base_url and self.base_url in texto:
            texto = texto.replace(self.base_url, "[evolution]")
        return texto

    def _request(self, metodo, caminho, json=None):
        url = f"{self.base_url}{caminho}"
        try:
            resposta = requests.request(
                metodo, url, headers=self.headers, json=json, timeout=self.timeout
            )
        except requests.RequestException:
            # Não encadeia a exceção original: o repr do requests carrega a URL.
            raise EvolutionAPIError(
                "Não foi possível comunicar com a Evolution API."
            ) from None

        if resposta.status_code >= 400:
            raise EvolutionAPIError(
                f"A Evolution API retornou o status HTTP {resposta.status_code}."
            )

        try:
            return resposta.json()
        except (requests.exceptions.JSONDecodeError, ValueError):
            return {}

    # ------------------------------------------------------------- operações
    def criar_instancia(self, numero=None):
        corpo = {
            "instanceName": self.instance_name,
            "integration": "WHATSAPP-BAILEYS",
            "qrcode": True,
        }
        if numero:
            corpo["number"] = numero
        return self._request("POST", "/instance/create", json=corpo)

    def buscar_estado_conexao(self):
        return self._request(
            "GET", f"/instance/connectionState/{self.instance_name}"
        )

    def obter_qrcode(self):
        return self._request("GET", f"/instance/connect/{self.instance_name}")

    def logout(self):
        return self._request("DELETE", f"/instance/logout/{self.instance_name}")

    def deletar_instancia(self):
        return self._request("DELETE", f"/instance/delete/{self.instance_name}")

    def enviar_texto(self, telefone, texto):
        # Sem prévia: gerá-la faria a Evolution abrir o link de acesso do
        # responsável antes da família.
        corpo = {"number": telefone, "text": texto, "linkPreview": False}
        return self._request(
            "POST", f"/message/sendText/{self.instance_name}", json=corpo
        )

    def enviar_botoes(self, telefone, titulo, descricao, botoes, rodape=""):
        """Mensagem interativa com botões (ex.: {"type": "url", "displayText":
        ..., "url": ...}). Não é recurso oficial do WhatsApp: aparece nos apps
        de celular atuais, mas o WhatsApp Web costuma não mostrar os botões."""
        corpo = {"number": telefone, "title": titulo, "description": descricao, "buttons": botoes}
        if rodape:
            corpo["footer"] = rodape
        return self._request(
            "POST", f"/message/sendButtons/{self.instance_name}", json=corpo
        )


def client_para_config(config, timeout=None):
    """Monta um EvolutionClient a partir do IntegracaoWhatsApp da academia."""
    if config is None:
        raise EvolutionConfigError(
            "Integração de WhatsApp não configurada para esta academia."
        )
    api_key = resolver_credencial(config)
    return EvolutionClient(
        base_url=config.evolution_base_url,
        instance_name=config.evolution_instance_name,
        api_key=api_key,
        timeout=timeout,
    )
