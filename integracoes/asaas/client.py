import os

import requests
from dotenv import load_dotenv


load_dotenv()


class AsaasAPIError(RuntimeError):
    """Erro seguro e previsível ao comunicar com a API do Asaas."""


class AsaasClient:
    def __init__(self):
        self.base_url = os.getenv("ASAAS_BASE_URL")
        self.api_key = os.getenv("ASAAS_API_KEY")

        if not self.base_url:
            raise ValueError("ASAAS_BASE_URL não configurada.")

        if not self.api_key:
            raise ValueError("ASAAS_API_KEY não configurada.")

        self.base_url = self.base_url.rstrip("/")

        self.headers = {
            "access_token": self.api_key,
            "Content-Type": "application/json",
        }

    @staticmethod
    def _validar_resposta(response):
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            status = response.status_code
            raise AsaasAPIError(
                f"A API do Asaas retornou o status HTTP {status}."
            ) from exc

    @staticmethod
    def _obter_json(response):
        try:
            return response.json()
        except (requests.exceptions.JSONDecodeError, ValueError) as exc:
            raise AsaasAPIError(
                "A API do Asaas retornou uma resposta inválida."
            ) from exc

    @staticmethod
    def _erro_de_conexao(exc):
        raise AsaasAPIError(
            "Não foi possível comunicar com a API do Asaas."
        ) from exc

    def listar_clientes(self):
        url = f"{self.base_url}/customers"

        try:
            response = requests.get(
                url,
                headers=self.headers,
                timeout=30,
            )
        except requests.RequestException as exc:
            self._erro_de_conexao(exc)

        self._validar_resposta(response)

        return self._obter_json(response)

    def criar_cliente(self, nome, cpf_cnpj, telefone=None, email=None):
        url = f"{self.base_url}/customers"

        dados = {
            "name": nome,
            "cpfCnpj": cpf_cnpj,
        }

        if telefone:
            dados["mobilePhone"] = telefone

        if email:
            dados["email"] = email

        try:
            response = requests.post(
                url,
                headers=self.headers,
                json=dados,
                timeout=30,
            )
        except requests.RequestException as exc:
            self._erro_de_conexao(exc)

        self._validar_resposta(response)

        return self._obter_json(response)

    def criar_cobranca(
        self,
        customer,
        valor,
        vencimento,
        descricao,
        billing_type="PIX",
    ):
        url = f"{self.base_url}/payments"
        dados = {
            "customer": customer,
            "billingType": billing_type,
            "value": float(valor),
            "dueDate": vencimento.isoformat(),
            "description": descricao,
        }

        try:
            response = requests.post(
                url,
                headers=self.headers,
                json=dados,
                timeout=30,
            )
        except requests.RequestException as exc:
            self._erro_de_conexao(exc)

        self._validar_resposta(response)

        return self._obter_json(response)

    def buscar_cliente_por_cpf_cnpj(self, cpf_cnpj):
        url = f"{self.base_url}/customers"

        try:
            response = requests.get(
                url,
                headers=self.headers,
                params={
                    "cpfCnpj": cpf_cnpj,
                },
                timeout=30,
            )
        except requests.RequestException as exc:
            self._erro_de_conexao(exc)

        self._validar_resposta(response)

        resultado = self._obter_json(response)

        if resultado.get("data"):
            return resultado["data"][0]

        return None

    def buscar_cliente_por_id(self, customer_id):
        url = f"{self.base_url}/customers/{customer_id}"

        try:
            response = requests.get(
                url,
                headers=self.headers,
                timeout=30,
            )
        except requests.RequestException as exc:
            self._erro_de_conexao(exc)

        if response.status_code == 404:
            return None

        self._validar_resposta(response)

        return self._obter_json(response)
