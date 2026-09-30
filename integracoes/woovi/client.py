"""Cliente HTTP da API da Woovi (Pix, subcontas e saques).

Referência: https://developers.woovi.com/api (OpenAPI em
https://api.woovi.com/api/openapi.json). Produção em https://api.woovi.com,
testes em https://api.woovi-sandbox.com.

Só transporte: autenticação, timeout, tradução de erros e normalização das
respostas. Regra de negócio (mensalidade, repasse) fica em ``services``.
"""
import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote

import requests
from django.conf import settings
from django.utils.dateparse import parse_datetime
from django.views.decorators.debug import sensitive_variables

from integracoes.woovi.exceptions import (
    WooviAuthError,
    WooviConfigError,
    WooviInvalidResponseError,
    WooviNotFoundError,
    WooviRequestError,
    WooviTimeoutError,
    WooviUnavailableError,
)

_NAO_ENCONTRADO = re.compile(r"not found|n[aã]o encontrad", re.IGNORECASE)


@dataclass(frozen=True)
class Subconta:
    pix_key: str
    nome: str
    saldo_centavos: int
    saque_bloqueado: bool


@dataclass(frozen=True)
class Cobranca:
    correlation_id: str
    status: str
    valor_centavos: int
    br_code: str
    link_pagamento: str
    expira_em: Optional[object]
    transaction_id: str
    pago_em: Optional[object]
    taxa_centavos: Optional[int] = None  # "fee" — nem sempre vem no GET


@dataclass(frozen=True)
class Saque:
    status: str
    valor_centavos: int
    correlation_id: str
    end_to_end_id: str
    destino: str


@dataclass(frozen=True)
class LancamentoExtrato:
    id: str
    momento: Optional[object]
    operacao: str
    valor_centavos: int
    saldo_centavos: Optional[int]


def _caminho(valor):
    """Chave Pix / correlationID no caminho da URL (e-mail, +55...)."""
    return quote(str(valor), safe="")


def _centavos(valor):
    try:
        return int(round(float(valor)))
    except (TypeError, ValueError):
        return 0


class WooviClient:
    @sensitive_variables()
    def __init__(self, *, conta=None, contexto="legado", credencial_ref=None, base_url=None):
        from .credenciais import da_conta, referencia_plataforma, resolver

        self.contexto = contexto
        if conta is not None:
            self.contexto = "legado" if conta.legada else "academia"
            app_id = da_conta(conta)
            base_url = conta.api_base_url or getattr(settings, "WOOVI_BASE_URL", "")
        elif contexto == "publico":
            app_id = ""
        elif contexto == "plataforma":
            ref = credencial_ref or referencia_plataforma()
            if ref not in ("WOOVI_APP_ID", "WOOVI_PLATAFORMA_APP_ID"):
                raise WooviConfigError("Credencial inválida para assinatura da plataforma.")
            app_id = resolver(ref)
            if base_url is None and ref == "WOOVI_PLATAFORMA_APP_ID":
                base_url = getattr(settings, "WOOVI_PLATAFORMA_BASE_URL", "")
        elif contexto == "onboarding":
            app_id = resolver("WOOVI_ONBOARDING_APP_ID")
            base_url = getattr(settings, "WOOVI_ONBOARDING_BASE_URL", "")
        elif contexto == "legado":
            app_id = getattr(settings, "WOOVI_APP_ID", "")
        else:
            raise WooviConfigError("Contexto da integração inválido.")
        self.base_url = (base_url if base_url is not None else getattr(settings, "WOOVI_BASE_URL", "")).rstrip("/")

        if not self.base_url:
            raise WooviConfigError("WOOVI_BASE_URL não configurada.")
        if not app_id and contexto != "publico":
            raise WooviConfigError("WOOVI_APP_ID não configurado.")

        # A Woovi recebe o AppID cru, sem o prefixo "Bearer". Ele vive só
        # neste header: nunca entra em mensagem de erro, log ou repr.
        self._headers = {
            "Authorization": app_id,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        self.timeout = getattr(settings, "WOOVI_TIMEOUT", 30)

    def __repr__(self):
        return f"<WooviClient {self.contexto}>"

    # ------------------------------------------------------------ transporte
    @sensitive_variables()
    def _request(self, metodo, caminho, *, autenticar=True, **kwargs):
        if "/subaccount" in caminho and self.contexto != "legado":
            raise WooviConfigError("Operação exclusiva do recebimento legado.")
        if caminho.startswith("/api/v1/charge") and self.contexto not in ("legado", "academia", "plataforma"):
            raise WooviConfigError("Este contexto não pode operar cobranças.")
        if caminho.startswith("/api/v1/partner") and self.contexto != "onboarding":
            raise WooviConfigError("Operação exclusiva da credencial de parceiro.")
        headers = self._headers if autenticar else {"Accept": "application/json"}
        try:
            response = requests.request(
                metodo,
                f"{self.base_url}{caminho}",
                headers=headers,
                timeout=self.timeout,
                allow_redirects=False,
                **kwargs,
            )
        except requests.Timeout:
            raise WooviTimeoutError(
                "A Woovi não respondeu a tempo; o resultado da operação é desconhecido."
            ) from None
        except requests.RequestException:
            raise WooviUnavailableError(
                "Não foi possível comunicar com a Woovi."
            ) from None

        if 300 <= response.status_code < 400:
            raise WooviInvalidResponseError("Redirecionamento inesperado do provedor.")
        if response.status_code >= 400:
            raise self._erro_http(response)

        try:
            return response.json()
        except ValueError:
            raise WooviInvalidResponseError(
                "A Woovi retornou uma resposta inválida.", response.status_code
            ) from None

    def _erro_http(self, response):
        status = response.status_code
        detalhe = ""
        try:
            corpo = response.json()
        except ValueError:
            corpo = None
        if isinstance(corpo, dict) and isinstance(corpo.get("error"), str):
            detalhe = corpo["error"]
            # Respostas externas podem ecoar cabeçalhos. Nunca propagar isso.
            for segredo in self._headers.values():
                if segredo:
                    detalhe = detalhe.replace(segredo, "[redigido]")
            detalhe = re.sub(r"https?://\S+", "[url]", detalhe)[:200]

        mensagem = f"A Woovi retornou o status HTTP {status}" + (f": {detalhe}" if detalhe else "") + "."
        if status in (401, 403):
            return WooviAuthError(mensagem, status)
        if status == 404 or (status == 400 and _NAO_ENCONTRADO.search(detalhe)):
            return WooviNotFoundError(mensagem, status)
        if status >= 500:
            return WooviUnavailableError(mensagem, status)
        return WooviRequestError(mensagem, status)

    @staticmethod
    def _objeto(resposta, *chaves):
        """Primeiro objeto encontrado sob uma das chaves (a documentação da
        Woovi varia a capitalização/aninhamento entre exemplos)."""
        if isinstance(resposta, dict):
            for chave in chaves:
                valor = resposta
                for parte in chave.split("."):
                    valor = valor.get(parte) if isinstance(valor, dict) else None
                if isinstance(valor, dict):
                    return valor
        raise WooviInvalidResponseError("A resposta da Woovi não tem o formato esperado.")

    # ------------------------------------------------------------- subcontas
    @classmethod
    def _subconta(cls, resposta):
        dados = cls._objeto(resposta, "SubAccount", "subAccount", "subaccount")
        pix_key = dados.get("pixKey")
        if not pix_key:
            raise WooviInvalidResponseError("A resposta da Woovi não contém a subconta.")
        return Subconta(
            pix_key=pix_key,
            nome=dados.get("name") or "",
            saldo_centavos=_centavos(dados.get("balance")),
            saque_bloqueado=bool(dados.get("withdrawBlocked")),
        )

    def listar_subcontas(self):
        """Subcontas da conta (somente leitura)."""
        resposta = self._request("GET", "/api/v1/subaccount")
        # O schema documenta "subaccounts"; o exemplo, "subAccounts".
        itens = resposta.get("subaccounts") if isinstance(resposta, dict) else None
        if itens is None and isinstance(resposta, dict):
            itens = resposta.get("subAccounts")
        if not isinstance(itens, list):
            raise WooviInvalidResponseError("A Woovi retornou uma lista de subcontas inválida.")
        return [self._subconta({"SubAccount": item}) for item in itens if isinstance(item, dict)]

    def criar_ou_obter_subconta(self, pix_key, nome):
        """Cria a subconta da chave Pix, ou devolve a existente."""
        resposta = self._request(
            "POST", "/api/v1/subaccount", json={"pixKey": pix_key, "name": nome}
        )
        return self._subconta(resposta)

    def obter_subconta(self, pix_key):
        return self._subconta(self._request("GET", f"/api/v1/subaccount/{_caminho(pix_key)}"))

    def creditar_subconta(self, pix_key, valor_centavos, descricao=""):
        """Transfere ``valor_centavos`` da conta principal para a subconta.
        NÃO é idempotente (não aceita correlationID): depois de um timeout,
        confira o extrato da subconta antes de repetir."""
        corpo = {"value": int(valor_centavos)}
        if descricao:
            corpo["description"] = descricao[:140]
        return self._request("POST", f"/api/v1/subaccount/{_caminho(pix_key)}/credit", json=corpo)

    def sacar_subconta(self, pix_key, valor_centavos):
        """Saca da subconta para a própria chave Pix dela. NÃO é idempotente
        e responde antes da confirmação (status CREATED): a confirmação chega
        pelos webhooks OPENPIX:MOVEMENT_CONFIRMED/FAILED. A tarifa de saque
        sai do saldo além do valor pedido (ver repasses.valor_do_saque)."""
        resposta = self._request(
            "POST",
            f"/api/v1/subaccount/{_caminho(pix_key)}/withdraw",
            json={"value": int(valor_centavos)},
        )
        # O schema documenta withdraw.account; o exemplo, transaction.
        dados = self._objeto(resposta, "transaction", "withdraw.account", "withdraw")
        return Saque(
            status=str(dados.get("status") or ""),
            valor_centavos=_centavos(dados.get("value")),
            correlation_id=dados.get("correlationID") or "",
            end_to_end_id=dados.get("endToEndId") or "",
            destino=dados.get("destinationAlias") or "",
        )

    def extrato_subconta(self, pix_key):
        resposta = self._request("GET", f"/api/v1/subaccount/{_caminho(pix_key)}/statement")
        if isinstance(resposta, dict):
            resposta = resposta.get("statement") or resposta.get("data") or []
        if not isinstance(resposta, list):
            raise WooviInvalidResponseError("A Woovi retornou um extrato inválido.")
        lancamentos = []
        for item in resposta:
            if not isinstance(item, dict):
                continue
            lancamentos.append(LancamentoExtrato(
                id=str(item.get("id") or ""),
                momento=parse_datetime(item.get("time") or ""),
                operacao=item.get("operationType") or item.get("type") or "",
                valor_centavos=_centavos(item.get("value")),
                saldo_centavos=None if item.get("balance") is None else _centavos(item.get("balance")),
            ))
        return lancamentos

    # ------------------------------------------------------------- cobranças
    @classmethod
    def _cobranca(cls, resposta):
        dados = cls._objeto(resposta, "charge")
        return Cobranca(
            correlation_id=dados.get("correlationID") or "",
            status=dados.get("status") or "",
            valor_centavos=_centavos(dados.get("value")),
            br_code=dados.get("brCode") or "",
            link_pagamento=dados.get("paymentLinkUrl") or "",
            expira_em=parse_datetime(dados.get("expiresDate") or ""),
            transaction_id=dados.get("transactionID") or "",
            pago_em=parse_datetime(dados.get("paidAt") or ""),
            taxa_centavos=None if dados.get("fee") is None else _centavos(dados.get("fee")),
        )

    def criar_cobranca(
        self,
        *,
        correlation_id,
        valor_centavos,
        comentario="",
        expira_em_segundos=None,
        cliente=None,
        splits=None,
    ):
        """Cria uma cobrança Pix. ``return_existing`` torna a chamada
        idempotente: repetir o mesmo correlationID devolve a cobrança já
        criada em vez de falhar ou duplicar."""
        corpo = {
            "correlationID": correlation_id,
            "value": int(valor_centavos),
        }
        if comentario:
            corpo["comment"] = comentario
        if expira_em_segundos:
            corpo["expiresIn"] = int(expira_em_segundos)
        if cliente:
            corpo["customer"] = cliente
        if splits:
            corpo["splits"] = splits

        resposta = self._request(
            "POST",
            "/api/v1/charge",
            params={"return_existing": "true"},
            json=corpo,
        )
        return self._cobranca(resposta)

    def obter_cobranca(self, correlation_id):
        return self._cobranca(self._request("GET", f"/api/v1/charge/{_caminho(correlation_id)}"))

    def remover_cobranca(self, correlation_id):
        """Exclui a cobrança (o Pix deixa de aceitar pagamento)."""
        self._request("DELETE", f"/api/v1/charge/{_caminho(correlation_id)}")

    # --------------------------------------------------------------- webhook
    def chaves_publicas_webhook(self):
        """PEMs das chaves que assinam os webhooks (x-webhook-signature).
        Durante uma rotação vem mais de uma; qualquer uma vale. O endpoint
        é público — não envia o AppID."""
        resposta = self._request("GET", "/api/v1/webhook/public-keys", autenticar=False)
        chaves = resposta.get("public_keys") if isinstance(resposta, dict) else None
        if not isinstance(chaves, list):
            raise WooviInvalidResponseError("A Woovi retornou uma lista de chaves inválida.")
        pems = [c["key"] for c in chaves if isinstance(c, dict) and isinstance(c.get("key"), str)]
        if not pems:
            raise WooviInvalidResponseError("A Woovi não retornou nenhuma chave pública.")
        return pems

    def listar_contas(self):
        resposta = self._request("GET", "/api/v1/account", params={"limit": 100})
        contas = resposta.get("accounts") if isinstance(resposta, dict) else None
        if not isinstance(contas, list):
            raise WooviInvalidResponseError("Não foi possível identificar a conta de recebimento.")
        return contas

    def obter_conta(self, account_id):
        return self._objeto(self._request("GET", f"/api/v1/account/{_caminho(account_id)}"), "account")

    def configurar_webhook(self, url, nome):
        existentes = self._request("GET", "/api/v1/webhook", params={"url": url})
        hooks = existentes.get("webhooks") if isinstance(existentes, dict) else None
        if not isinstance(hooks, list):
            raise WooviInvalidResponseError("Não foi possível verificar a conexão de notificações.")
        for hook in hooks:
            if (isinstance(hook, dict) and hook.get("url") == url
                    and hook.get("event") == "OPENPIX:CHARGE_COMPLETED"
                    and hook.get("isActive") is True):
                return hook
        resposta = self._request("POST", "/api/v1/webhook", json={"webhook": {
            "name": nome, "event": "OPENPIX:CHARGE_COMPLETED", "url": url, "isActive": True,
        }})
        webhook = self._objeto(resposta, "webhook")
        if (webhook.get("url") != url or webhook.get("event") != "OPENPIX:CHARGE_COMPLETED"
                or webhook.get("isActive") is not True):
            raise WooviInvalidResponseError("O provedor não confirmou a conexão de notificações.")
        return webhook

    def iniciar_onboarding(self, *, cnpj, correlation_id, nome, redirect_url):
        # partner=True cria uma empresa afiliada própria, não uma conta BaaS
        # da plataforma. Requer PARTNER + KYC_ONBOARDING_LINK na Woovi.
        return self._request("POST", "/api/v1/kyc/onboarding", json={
            "taxID": cnpj, "correlationID": correlation_id, "partner": True,
            "company": {"name": nome}, "redirectUrl": redirect_url,
        })

    def consultar_onboarding(self, correlation_id):
        return self._request("GET", f"/api/v1/account-register/{_caminho(correlation_id)}")

    # ---------------------------------------------------------- Partner API
    def consultar_empresa_parceira(self, cnpj):
        """Pré-cadastro/afiliada gerida pela conta parceira, pelo CNPJ."""
        resposta = self._request("GET", f"/api/v1/partner/company/{_caminho(cnpj)}")
        return self._objeto(resposta, "preRegistration")

    def listar_afiliadas(self):
        resposta = self._request("GET", "/api/v1/partner/affiliate")
        afiliadas = resposta.get("affiliates") if isinstance(resposta, dict) else None
        if not isinstance(afiliadas, list):
            raise WooviInvalidResponseError("A Woovi retornou uma lista de afiliadas inválida.")
        return afiliadas

    @sensitive_variables()
    def criar_application_afiliada(self, *, cnpj, nome, escopos):
        """Cria (ou devolve, idempotente) a application da afiliada.

        A resposta traz clientId e clientSecret: quem chama monta o AppID
        (partner.montar_app_id) e o guarda cifrado. Nunca registre o retorno.
        """
        from .partner import validar_escopos

        return self._request("POST", "/api/v1/partner/application", json={
            "application": {"name": nome[:60], "type": "API", "scopes": list(validar_escopos(escopos))},
            "taxID": {"taxID": cnpj, "type": "BR:CNPJ"},
        })
