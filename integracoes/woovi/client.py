"""Cliente HTTP da API da Woovi (Pix).

Referência: https://developers.woovi.com/api (OpenAPI em
https://api.woovi.com/api/openapi.json). Produção em https://api.woovi.com,
testes em https://api.woovi-sandbox.com.
"""
from urllib.parse import quote

import requests
from django.conf import settings


class WooviAPIError(RuntimeError):
    """Erro seguro e previsível ao comunicar com a API da Woovi."""


class WooviClient:
    def __init__(self):
        self.base_url = (getattr(settings, "WOOVI_BASE_URL", "") or "").rstrip("/")
        self.app_id = getattr(settings, "WOOVI_APP_ID", "")

        if not self.base_url:
            raise ValueError("WOOVI_BASE_URL não configurada.")

        if not self.app_id:
            raise ValueError("WOOVI_APP_ID não configurado.")

        self.headers = {
            # A Woovi recebe o AppID cru, sem o prefixo "Bearer".
            "Authorization": self.app_id,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        self.timeout = getattr(settings, "WOOVI_TIMEOUT", 30)

    def _request(self, metodo, caminho, **kwargs):
        try:
            response = requests.request(
                metodo,
                f"{self.base_url}{caminho}",
                headers=self.headers,
                timeout=self.timeout,
                **kwargs,
            )
        except requests.RequestException as exc:
            raise WooviAPIError(
                "Não foi possível comunicar com a API da Woovi."
            ) from exc

        if response.status_code >= 400:
            raise WooviAPIError(self._mensagem_de_erro(response))

        try:
            return response.json()
        except ValueError as exc:
            raise WooviAPIError(
                "A API da Woovi retornou uma resposta inválida."
            ) from exc

    @staticmethod
    def _mensagem_de_erro(response):
        """A Woovi responde erros como {"error": "..."}; o texto é do
        negócio (ex.: cobrança já paga), não carrega credenciais."""
        detalhe = ""
        try:
            corpo = response.json()
        except ValueError:
            corpo = None
        if isinstance(corpo, dict) and isinstance(corpo.get("error"), str):
            detalhe = f": {corpo['error'][:200]}"
        return f"A API da Woovi retornou o status HTTP {response.status_code}{detalhe}."

    @staticmethod
    def _cobranca(resposta):
        cobranca = resposta.get("charge") if isinstance(resposta, dict) else None
        if not isinstance(cobranca, dict):
            raise WooviAPIError("A resposta da Woovi não contém a cobrança.")
        return cobranca

    def criar_cobranca(
        self,
        *,
        correlation_id,
        valor_centavos,
        comentario="",
        expira_em_segundos=None,
        cliente=None,
    ):
        """Cria uma cobrança Pix. ``return_existing`` torna a chamada
        idempotente: repetir o mesmo correlationID devolve a cobrança já
        criada em vez de falhar ou duplicar."""
        corpo = {
            "correlationID": correlation_id,
            "value": valor_centavos,
        }
        if comentario:
            corpo["comment"] = comentario
        if expira_em_segundos:
            corpo["expiresIn"] = expira_em_segundos
        if cliente:
            corpo["customer"] = cliente

        resposta = self._request(
            "POST",
            "/api/v1/charge",
            params={"return_existing": "true"},
            json=corpo,
        )
        return self._cobranca(resposta)

    def obter_cobranca(self, correlation_id):
        resposta = self._request(
            "GET", f"/api/v1/charge/{quote(correlation_id, safe='')}"
        )
        return self._cobranca(resposta)

    def remover_cobranca(self, correlation_id):
        """Exclui a cobrança (o Pix deixa de aceitar pagamento)."""
        return self._request(
            "DELETE", f"/api/v1/charge/{quote(correlation_id, safe='')}"
        )
