"""Cliente HTTP para o webhook do n8n.

Preparado, porém não acionado no fluxo principal (FASE 1). Erros são
sanitizados: nunca incluem a URL do webhook nem o token na mensagem.
"""
import requests
from django.conf import settings


class N8nAPIError(RuntimeError):
    """Erro seguro e previsível ao comunicar com o webhook do n8n."""


class N8nClient:
    def __init__(self, webhook_url=None, token=None, timeout=None):
        self.webhook_url = webhook_url or getattr(settings, "N8N_WEBHOOK_URL", "")
        self.token = token or getattr(settings, "N8N_TOKEN", "")
        self.timeout = timeout or getattr(settings, "N8N_TIMEOUT", 10)

        if not self.webhook_url:
            raise ValueError("N8N_WEBHOOK_URL não configurada.")

        self.headers = {"Content-Type": "application/json"}
        if self.token:
            self.headers["Authorization"] = f"Bearer {self.token}"

    def _sanitizar(self, mensagem):
        """Garante que URL/token nunca vazem numa exceção."""
        texto = str(mensagem)
        if self.webhook_url and self.webhook_url in texto:
            texto = texto.replace(self.webhook_url, "[webhook]")
        if self.token and self.token in texto:
            texto = texto.replace(self.token, "[token]")
        return texto

    def enviar(self, payload):
        """POST do payload no webhook do n8n. Devolve o JSON de resposta
        (ou {} quando não houver corpo JSON). Levanta N8nAPIError em
        qualquer falha, com mensagem sanitizada."""
        try:
            response = requests.post(
                self.webhook_url,
                headers=self.headers,
                json=payload,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise N8nAPIError(
                "Não foi possível comunicar com o webhook do n8n."
            ) from None

        if response.status_code >= 400:
            raise N8nAPIError(
                f"O webhook do n8n retornou o status HTTP {response.status_code}."
            )

        try:
            return response.json()
        except (requests.exceptions.JSONDecodeError, ValueError):
            return {}
