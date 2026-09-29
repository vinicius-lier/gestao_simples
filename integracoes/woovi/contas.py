"""Conexão da conta própria e onboarding hospedado pela Woovi.

Nenhum segredo é recebido de formulários. O operador provisiona a variável
por academia no servidor; a academia inicia/verifica/ativa pelo portal.
"""
import os
import re
from urllib.parse import urlsplit

from django.conf import settings
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from academias.models import Academia
from financeiro.models import ContaRecebimento
from .client import WooviClient
from .credenciais import da_conta, fingerprint, referencia_academia
from .exceptions import WooviConfigError, WooviInvalidResponseError

TEXTO_TAXA = "Taxa de processamento Pix: R$ 0,85 por Pix recebido."
EXPLICACAO_TAXA = (
    "Essa taxa é cobrada pela Woovi diretamente sobre os pagamentos processados "
    "e não é receita do sistema Keiko Fukuda."
)
BASES = {"https://api.woovi.com", "https://api.woovi-sandbox.com"}
HOSTS_KYC = {"kyc.woovi.com", "kyc.woovi-sandbox.com"}


def url_publica(caminho):
    base = settings.SITE_URL.rstrip("/")
    partes = urlsplit(base)
    if partes.scheme != "https" or not partes.hostname or partes.username or partes.password or partes.query or partes.fragment:
        raise WooviConfigError("Configure o endereço HTTPS público do sistema.")
    return base + caminho


def link_kyc_seguro(url):
    try:
        partes = urlsplit(url)
        return (partes.scheme == "https" and partes.hostname in HOSTS_KYC
                and partes.port in (None, 443) and not partes.username and not partes.password
                and partes.path.startswith("/onboarding/"))
    except (TypeError, ValueError):
        return False


def preparar_conta(academia, usuario=None):
    with transaction.atomic():
        Academia.objects.select_for_update().get(pk=academia.pk)
        ref = referencia_academia(academia.pk)
        base = os.getenv(ref.replace("_APP_ID", "_BASE_URL"), settings.WOOVI_ACADEMIAS_BASE_URL).rstrip("/")
        if base not in BASES:
            raise WooviConfigError("Ambiente da conta própria inválido.")
        conta, _ = ContaRecebimento.objects.get_or_create(
            academia=academia, modelo_recebimento=ContaRecebimento.CONTA_PROPRIA,
            defaults={
                "ativa": False, "credencial_ref": ref, "api_base_url": base,
                "status": ContaRecebimento.CONFIGURANDO, "criada_por": usuario,
            },
        )
        if not conta.onboarding_correlation_id:
            conta.onboarding_correlation_id = f"academia-{academia.pk}-conta-{conta.pk}"
            conta.save(update_fields=["onboarding_correlation_id"])
        return conta


def iniciar_onboarding(conta):
    if conta.legada or conta.conectada_em:
        raise ValueError("Esta conta não precisa iniciar um cadastro.")
    cnpj = re.sub(r"\D", "", conta.academia.cnpj)
    if len(cnpj) != 14:
        raise ValueError("Confira o CNPJ da academia antes de abrir a conta.")
    resposta = WooviClient(contexto="onboarding").iniciar_onboarding(
        cnpj=cnpj, correlation_id=conta.onboarding_correlation_id,
        nome=conta.academia.nome, redirect_url=url_publica(reverse("portal:recebimento")),
    )
    if not isinstance(resposta, dict) or not link_kyc_seguro(resposta.get("linkOnboarding", "")):
        raise WooviInvalidResponseError("O provedor não retornou um link seguro para o cadastro.")
    dados = resposta.get("accountRegister") or {}
    _validar_cadastro(conta, dados)
    conta.onboarding_url = resposta["linkOnboarding"]
    conta.onboarding_status = str(dados.get("status", "PENDING"))[:40]
    conta.status = _status_cadastro(conta.onboarding_status)
    conta.save(update_fields=["onboarding_url", "onboarding_status", "status"])
    return conta


def _validar_cadastro(conta, dados):
    if not isinstance(dados, dict):
        raise WooviInvalidResponseError("Cadastro não identificado.")
    documento = dados.get("taxID") or {}
    if isinstance(documento, dict):
        documento = documento.get("taxID", "")
    if (dados.get("correlationID") != conta.onboarding_correlation_id
            or re.sub(r"\D", "", str(documento)) != re.sub(r"\D", "", conta.academia.cnpj)):
        raise WooviInvalidResponseError("O cadastro retornado não pertence à academia.")


def _status_cadastro(status):
    if status in ("REJECTED", "FAILED"):
        return ContaRecebimento.ERRO
    if status in ("IN_REVIEW", "APPROVED", "CREATING"):
        return ContaRecebimento.AGUARDANDO
    return ContaRecebimento.CONFIGURANDO


def atualizar_onboarding(conta):
    if conta.legada or not conta.onboarding_url:
        raise ValueError("Inicie o cadastro antes de consultar a análise.")
    dados = WooviClient(contexto="onboarding").consultar_onboarding(conta.onboarding_correlation_id)
    _validar_cadastro(conta, dados)
    conta.onboarding_status = str(dados.get("status", "PENDING"))[:40]
    if not conta.conectada_em:
        conta.status = _status_cadastro(conta.onboarding_status)
    conta.save(update_fields=["onboarding_status", "status"])
    return conta


def ativar_conta(conta, usuario=None):
    if conta.legada:
        raise ValueError("Selecione a conta própria da academia.")
    if conta.onboarding_url and conta.onboarding_status != "APPROVED":
        atualizar_onboarding(conta)
        if conta.onboarding_status != "APPROVED":
            raise ValueError("O cadastro ainda aguarda aprovação na Woovi.")
    # Valida a credencial antes de qualquer operação remota. Nunca usar uma
    # credencial principal como substituta quando faltar a da academia.
    digest = fingerprint(da_conta(conta))
    client = WooviClient(conta=conta)
    contas = client.listar_contas()
    padrao = [c for c in contas if isinstance(c, dict) and c.get("isDefault") is True]
    if len(contas) != 1 or len(padrao) != 1 or not padrao[0].get("accountId"):
        raise WooviInvalidResponseError("Não foi possível identificar uma única conta padrão.")
    account_id = str(padrao[0]["accountId"])
    dados = client.obter_conta(account_id)
    cnpj = re.sub(r"\D", "", conta.academia.cnpj)
    if len(cnpj) != 14 or re.sub(r"\D", "", str(dados.get("taxId", ""))) != cnpj:
        raise ValueError("O CNPJ da conta Woovi não corresponde ao da academia.")
    if dados.get("accountId") != account_id:
        raise WooviInvalidResponseError("A conta retornada não corresponde à integração.")
    if conta.provider_account_id and conta.provider_account_id != account_id:
        raise ValueError("A conta vinculada mudou. Solicite uma revisão antes de continuar.")

    with transaction.atomic():
        Academia.objects.select_for_update().get(pk=conta.academia_id)
        travada = ContaRecebimento.objects.select_for_update().get(pk=conta.pk)
        if fingerprint(da_conta(travada)) != digest:
            raise WooviConfigError("A credencial mudou durante a validação. Tente novamente.")
        # Serializa ativações da academia e reaproveita o callback em caso
        # de uma falha anterior após a criação remota do webhook.
        if not travada.conectada_em:
            client.configurar_webhook(
                url_publica(reverse("webhook_woovi_conta", args=[travada.pk])),
                f"keiko-academia-{travada.academia_id}-conta-{travada.pk}",
            )
        # Desativar como destino NOVO não muda as cobranças nem os repasses.
        ContaRecebimento.objects.filter(academia_id=conta.academia_id, ativa=True).exclude(pk=conta.pk).update(
            ativa=False, desativada_em=timezone.now(), desativada_por=usuario,
        )
        travada.provider_account_id = account_id
        travada.credencial_fingerprint = digest
        travada.status = ContaRecebimento.CONECTADA
        travada.ativa = True
        travada.conectada_em = travada.conectada_em or timezone.now()
        travada.taxa_ciente_em = timezone.now()
        travada.save()
        return travada
