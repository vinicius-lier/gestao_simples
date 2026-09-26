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
        external_reference=None,
    ):
        url = f"{self.base_url}/payments"
        dados = {
            "customer": customer,
            "billingType": billing_type,
            "value": float(valor),
            "dueDate": vencimento.isoformat(),
            "description": descricao,
        }

        # Identificador estável da cobrança do lado do sistema. Permite
        # reconciliar (ver listar_cobrancas) quando um timeout esconde a
        # resposta de uma criação que, no Asaas, foi concluída.
        if external_reference:
            dados["externalReference"] = external_reference

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

    def listar_cobrancas(self, external_reference=None, customer=None, status=None, limit=None):
        """Lista cobranças, opcionalmente filtrando por externalReference —
        usado para reconciliar uma criação cujo resultado se perdeu (timeout)
        sem gerar uma segunda cobrança."""
        url = f"{self.base_url}/payments"

        params = {}
        if external_reference:
            params["externalReference"] = external_reference
        if customer:
            params["customer"] = customer
        if status:
            params["status"] = status
        if limit:
            params["limit"] = limit

        try:
            response = requests.get(
                url,
                headers=self.headers,
                params=params,
                timeout=30,
            )
        except requests.RequestException as exc:
            self._erro_de_conexao(exc)

        self._validar_resposta(response)

        return self._obter_json(response)

    def buscar_cobranca_por_external_reference(self, external_reference):
        """Retorna a primeira cobrança com o externalReference informado, ou
        None. Base da reconciliação idempotente."""
        if not external_reference:
            return None

        resultado = self.listar_cobrancas(external_reference=external_reference)
        dados = resultado.get("data") if isinstance(resultado, dict) else None

        if dados:
            return dados[0]

        return None

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

    def obter_pix_qrcode(self, payment_id):
        url = f"{self.base_url}/payments/{payment_id}/pixQrCode"

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

    def remover_cobranca(self, payment_id):
        """Exclui a cobrança no Asaas (o link de pagamento deixa de aceitar
        Pix/boleto/cartão). Devolve None se ela já não existe lá."""
        url = f"{self.base_url}/payments/{payment_id}"

        try:
            response = requests.delete(
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
