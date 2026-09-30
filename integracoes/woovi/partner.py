"""Partner API da Woovi: peças sem regra de academia, aluno ou mensalidade.

A conta parceira (credencial ``WOOVI_ONBOARDING_APP_ID``) cadastra a empresa
afiliada pelo KYC hospedado, acompanha a análise e, depois da aprovação, cria
a application da afiliada. A Woovi devolve ``clientId`` e ``clientSecret``; o
AppID da afiliada é ``Base64(clientId:clientSecret)`` e é montado só aqui.

Endpoints (OpenAPI em https://api.woovi.com/api/openapi.json):
- POST /api/v1/kyc/onboarding (``partner: true``) e GET /api/v1/account-register/{id};
- GET /api/v1/partner/company/{taxID}, GET /api/v1/partner/affiliate;
- POST /api/v1/partner/application (escopo PARTNER_APPLICATION_POST).
"""
import base64

from django.views.decorators.debug import sensitive_variables

from .exceptions import WooviInvalidResponseError

# Etapas da conexão por parceiro, na ordem em que acontecem.
NAO_INICIADO = "NAO_INICIADO"
PRE_CADASTRO_CRIADO = "PRE_CADASTRO_CRIADO"
AGUARDANDO_KYC = "AGUARDANDO_KYC"
EM_ANALISE = "EM_ANALISE"
APROVADO = "APROVADO"
APPLICATION_CRIADA = "APPLICATION_CRIADA"
WEBHOOK_CONFIGURADO = "WEBHOOK_CONFIGURADO"
ATIVA = "ATIVA"
ERRO = "ERRO"
ETAPAS = [
    (NAO_INICIADO, "Não iniciado"),
    (PRE_CADASTRO_CRIADO, "Pré-cadastro criado"),
    (AGUARDANDO_KYC, "Aguardando verificação na Woovi"),
    (EM_ANALISE, "Em análise na Woovi"),
    (APROVADO, "Aprovado"),
    (APPLICATION_CRIADA, "Credencial criada"),
    (WEBHOOK_CONFIGURADO, "Notificações configuradas"),
    (ATIVA, "Ativa"),
    (ERRO, "Erro"),
]

# Status do account-register da Woovi -> etapa. Desconhecido fica em KYC.
_ETAPA_DO_CADASTRO = {
    "PENDING": AGUARDANDO_KYC,
    "SUBMITTED": EM_ANALISE,
    "IN_REVIEW": EM_ANALISE,
    "CREATING": EM_ANALISE,
    "APPROVED": APROVADO,
    "REJECTED": ERRO,
    "FAILED": ERRO,
}

# Mínimo que a afiliada precisa para receber: cobrança (inclusive cancelar),
# notificação e conferência da conta. Nunca saque, transferência ou débito.
ESCOPOS_AFILIADA = (
    "CHARGE_POST", "CHARGE_GET", "CHARGE_GET_LIST", "CHARGE_DELETE",
    "WEBHOOK_POST", "WEBHOOK_GET_LIST", "ACCOUNT_GET_LIST", "ACCOUNT_GET",
)
_ESCOPOS_PROIBIDOS = ("WITHDRAW", "TRANSFER", "DEBIT", "CREDIT", "REFUND", "PAYMENT")


def etapa_do_cadastro(status):
    """Etapa correspondente ao status do cadastro (account-register)."""
    return _ETAPA_DO_CADASTRO.get(str(status or "").upper(), AGUARDANDO_KYC)


def validar_escopos(escopos):
    """Recusa pedir à Woovi qualquer escopo que movimente dinheiro."""
    escopos = tuple(escopos)
    if not escopos or any(p in e.upper() for e in escopos for p in _ESCOPOS_PROIBIDOS):
        raise ValueError("Escopos da afiliada fora do mínimo permitido.")
    return escopos


@sensitive_variables()
def montar_app_id(client_id, client_secret):
    """AppID da afiliada: Base64(clientId:clientSecret), como documenta a Woovi."""
    if not isinstance(client_id, str) or not isinstance(client_secret, str) or not client_id or not client_secret:
        raise WooviInvalidResponseError("A Woovi não retornou a credencial da afiliada.")
    if ":" in client_id:
        raise WooviInvalidResponseError("Identificador da credencial em formato inesperado.")
    return base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()


@sensitive_variables()
def credencial_da_application(resposta):
    """(clientId, clientSecret) da resposta de POST /api/v1/partner/application."""
    dados = resposta.get("application") if isinstance(resposta, dict) else None
    if not isinstance(dados, dict):
        raise WooviInvalidResponseError("A Woovi não retornou a application da afiliada.")
    if dados.get("isActive") is False:
        raise WooviInvalidResponseError("A application da afiliada está inativa na Woovi.")
    return dados.get("clientId"), dados.get("clientSecret")
