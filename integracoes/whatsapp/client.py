import os

import requests
from dotenv import load_dotenv

load_dotenv()


class WhatsAppAPIError(RuntimeError):
    """Erro seguro e previsível ao comunicar com a API do WhatsApp (Meta Cloud API)."""


class WhatsAppClient:
    """Cliente fino para a WhatsApp Cloud API (Meta).

    Variáveis de ambiente:
      WHATSAPP_API_VERSION     — ex.: v21.0 (opcional, tem um padrão)
      WHATSAPP_PHONE_NUMBER_ID — ID do número remetente no Meta Business
      WHATSAPP_ACCESS_TOKEN    — token de acesso do app/sistema
    """

    def __init__(self):
        self.api_version = os.getenv("WHATSAPP_API_VERSION", "v21.0")
        self.phone_number_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
        self.access_token = os.getenv("WHATSAPP_ACCESS_TOKEN")

        if not self.phone_number_id:
            raise ValueError("WHATSAPP_PHONE_NUMBER_ID não configurado.")

        if not self.access_token:
            raise ValueError("WHATSAPP_ACCESS_TOKEN não configurado.")

        self.base_url = (
            f"https://graph.facebook.com/{self.api_version}/{self.phone_number_id}/messages"
        )
        self.headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _validar_resposta(response):
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            status = response.status_code
            raise WhatsAppAPIError(
                f"A API do WhatsApp retornou o status HTTP {status}."
            ) from exc

    @staticmethod
    def _obter_json(response):
        try:
            return response.json()
        except (requests.exceptions.JSONDecodeError, ValueError) as exc:
            raise WhatsAppAPIError(
                "A API do WhatsApp retornou uma resposta inválida."
            ) from exc

    @staticmethod
    def _erro_de_conexao(exc):
        raise WhatsAppAPIError(
            "Não foi possível comunicar com a API do WhatsApp."
        ) from exc

    def _enviar(self, payload):
        try:
            response = requests.post(
                self.base_url,
                headers=self.headers,
                json=payload,
                timeout=30,
            )
        except requests.RequestException as exc:
            self._erro_de_conexao(exc)

        self._validar_resposta(response)
        return self._obter_json(response)

    def enviar_template(self, telefone, nome_template, idioma="pt_BR", parametros=None):
        """Envia uma mensagem de template aprovada no Meta Business Manager.
        Fora da janela de 24h de uma conversa, é o único tipo de mensagem
        que a Meta permite iniciar por parte da empresa.

        `parametros`: lista de strings, uma para cada {{n}} do corpo do
        template, na ordem em que aparecem.
        """
        template = {"name": nome_template, "language": {"code": idioma}}
        if parametros:
            template["components"] = [
                {
                    "type": "body",
                    "parameters": [{"type": "text", "text": str(p)} for p in parametros],
                }
            ]
        return self._enviar({
            "messaging_product": "whatsapp",
            "to": telefone,
            "type": "template",
            "template": template,
        })

    def enviar_texto(self, telefone, texto):
        """Mensagem de texto livre. Só é aceita pela Meta dentro da janela
        de 24h de uma conversa iniciada pelo cliente — para o primeiro
        contato, use enviar_template."""
        return self._enviar({
            "messaging_product": "whatsapp",
            "to": telefone,
            "type": "text",
            "text": {"body": texto},
        })
