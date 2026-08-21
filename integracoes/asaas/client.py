import os

import requests
from dotenv import load_dotenv


load_dotenv()


class AsaasClient:
    def __init__(self):
        self.base_url = os.getenv("ASAAS_BASE_URL")
        self.api_key = os.getenv("ASAAS_API_KEY")

        if not self.base_url:
            raise ValueError("ASAAS_BASE_URL não configurada.")

        if not self.api_key:
            raise ValueError("ASAAS_API_KEY não configurada.")

        self.headers = {
            "access_token": self.api_key,
            "Content-Type": "application/json",
        }

    def listar_clientes(self):
        url = f"{self.base_url}/customers"

        response = requests.get(
            url,
            headers=self.headers,
            timeout=30,
        )

        response.raise_for_status()

        return response.json()

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

        response = requests.post(
            url,
            headers=self.headers,
            json=dados,
            timeout=30,
        )

        response.raise_for_status()

        return response.json()