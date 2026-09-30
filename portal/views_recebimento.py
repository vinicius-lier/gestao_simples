"""Configuração da conta própria; histórico anterior continua consultável.

A tela acompanha a conexão em quatro passos — abrir a conta na Woovi,
análise da Woovi, conexão automática e recebendo — e mostra um botão
principal por passo. O caminho manual (cadastro oficial + credencial
provisionada pelo operador) só aparece como fallback.
"""
import logging

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from financeiro.models import ContaRecebimento, Repasse
from integracoes.woovi import partner
from integracoes.woovi.contas import (
    EXPLICACAO_TAXA, TEXTO_TAXA, ativar_conta, atualizar_onboarding, automacao_disponivel,
    conectar_automaticamente, iniciar_onboarding, link_kyc_seguro, preparar_conta,
)
from integracoes.woovi.credenciais import provisionada_no_servidor
from integracoes.woovi.exceptions import WooviAuthError, WooviConfigError, WooviError
from .forms import ContaWooviForm
from .views import academia_required

logger = logging.getLogger(__name__)

PASSOS = ("Abrir conta na Woovi", "Análise da Woovi", "Conexão automática", "Recebendo")
# Ações que registram a ciência da taxa: abrir a conta e conectar.
ACOES_COM_CIENCIA = ("iniciar", "ativar")


def _registrar_ciencia(conta):
    ContaRecebimento.objects.filter(pk=conta.pk, taxa_ciente_em__isnull=True).update(taxa_ciente_em=timezone.now())
    conta.refresh_from_db()


def _concluir_conexao(request, conta):
    """Cadastro aprovado: termina a conexão pela Partner API. Uma falha aqui
    não marca a conta como erro — o cadastro continua aprovado e o fallback
    (credencial provisionada pelo operador) segue disponível."""
    try:
        conectar_automaticamente(conta, request.user)
    except WooviAuthError:
        logger.warning("Partner API recusou a conexão automática da conta %s.", conta.pk)
        messages.info(request, "Cadastro aprovado. A conexão automática ainda não está habilitada na Woovi; o suporte conclui a conexão pelo servidor.")
    except (WooviError, ValueError) as erro:
        logger.warning("Conexão automática da conta %s não concluída (%s).", conta.pk, type(erro).__name__)
        messages.warning(request, "Cadastro aprovado, mas não foi possível concluir a conexão agora. Tente de novo em instantes.")
    else:
        messages.success(request, "Cadastro aprovado e conta Woovi conectada. Novos Pix serão recebidos diretamente pela academia.")


def _passo(conta):
    """Índice (0–3) do passo atual e se ele está em erro."""
    if conta is None:
        return 0, False
    etapa = conta.etapa
    if etapa == partner.ATIVA:
        return 3, False
    if etapa in (partner.APROVADO, partner.APPLICATION_CRIADA, partner.WEBHOOK_CONFIGURADO):
        return 2, False
    if etapa == partner.EM_ANALISE:
        return 1, False
    if etapa == partner.ERRO:
        if conta.onboarding_status in ("REJECTED", "FAILED"):
            return 1, True
        return (2 if conta.onboarding_status == "APPROVED" else 0), True
    return 0, False


def _contexto_passos(conta, onboarding_url):
    passo, erro = _passo(conta)
    conectada = bool(conta and conta.status == ContaRecebimento.CONECTADA)
    automacao = bool(getattr(settings, "WOOVI_ONBOARDING_APP_ID", ""))
    credencial_pronta = bool(conta and not conta.legada and (
        conta.credencial_cifrada or provisionada_no_servidor(conta)))
    acoes = {
        "concluir": not conectada and passo == 2 and bool(onboarding_url) and automacao,
        "continuar": not conectada and passo in (0, 1) and bool(onboarding_url),
        "atualizar": not conectada and passo in (0, 1) and bool(onboarding_url),
        "iniciar": not conectada and passo == 0 and not onboarding_url and automacao,
        "cadastro_oficial": not conectada and passo == 0 and not onboarding_url and not automacao,
    }
    # Fallback do operador: credencial já no servidor, ou sem Partner API.
    acoes["verificar"] = not conectada and not acoes["concluir"] and (credencial_pronta or not automacao)
    acoes["verificar_principal"] = acoes["verificar"] and not (acoes["iniciar"] or acoes["continuar"])
    acoes["pede_ciencia"] = acoes["iniciar"] or acoes["concluir"] or acoes["verificar"]
    passos = []
    for indice, rotulo in enumerate(PASSOS):
        if conectada or indice < passo:
            estado = "feito"
        elif indice == passo:
            estado = "erro" if erro else "atual"
        else:
            estado = "pendente"
        passos.append((indice + 1, rotulo, estado))
    return {"passos": passos, "passo": passo, "automacao": automacao, "acoes": acoes}


@academia_required
@never_cache
@require_http_methods(["GET", "POST"])
def recebimento(request):
    if not request.administrador_academia:
        raise PermissionDenied("Somente o administrador da academia gerencia o recebimento.")
    conta = ContaRecebimento.objects.filter(
        academia=request.academia, modelo_recebimento=ContaRecebimento.CONTA_PROPRIA,
    ).first()
    acao = request.POST.get("acao") if request.method == "POST" else None
    form = ContaWooviForm(request.POST or None, exigir_confirmacao=acao in ACOES_COM_CIENCIA)
    if request.method == "POST" and form.is_valid():
        if acao not in ("iniciar", "ativar", "atualizar"):
            form.add_error(None, "Escolha uma ação de configuração.")
        else:
            try:
                conta = preparar_conta(request.academia, request.user)
                if acao == "iniciar":
                    if settings.WOOVI_ONBOARDING_APP_ID:
                        iniciar_onboarding(conta)
                        _registrar_ciencia(conta)
                        messages.success(request, "Cadastro iniciado. Continue no ambiente seguro da Woovi; depois da aprovação, a conexão é automática.")
                    else:
                        messages.info(request, "Abra a conta pelo cadastro oficial da Woovi. Depois, peça ao suporte a conexão com o sistema e use “Verificar e ativar”.")
                elif acao == "atualizar":
                    atualizar_onboarding(conta)
                    if conta.onboarding_status == "APPROVED" and not conta.conectada_em and automacao_disponivel(conta):
                        if conta.taxa_ciente_em:
                            _concluir_conexao(request, conta)
                        else:
                            messages.info(request, "Cadastro aprovado. Confirme a ciência da taxa e use “Concluir conexão”.")
                    else:
                        messages.success(request, "Situação do cadastro atualizada.")
                elif automacao_disponivel(conta) and not (conta.credencial_cifrada or provisionada_no_servidor(conta)):
                    _registrar_ciencia(conta)
                    _concluir_conexao(request, conta)
                else:
                    ativar_conta(conta, request.user)
                    messages.success(request, "Conta Woovi conectada. Novos Pix serão recebidos diretamente pela academia.")
                return redirect("portal:recebimento")
            except WooviConfigError:
                messages.error(request, "A conexão precisa ser preparada no servidor. Peça ao suporte para conferir a credencial exclusiva da academia e tente novamente.")
            except (WooviError, ValueError) as erro:
                if conta is not None and not conta.conectada_em:
                    ContaRecebimento.objects.filter(pk=conta.pk).update(status=ContaRecebimento.ERRO)
                    conta.refresh_from_db()
                logger.warning("Falha na configuração Woovi da academia %s (%s).", request.academia.pk, type(erro).__name__)
                messages.error(request, str(erro) if isinstance(erro, ValueError) else
                               "Não foi possível validar a conexão agora. Tente novamente ou fale com o suporte.")
    if conta is not None:
        conta.refresh_from_db()
    onboarding_url = conta.onboarding_url if conta and link_kyc_seguro(conta.onboarding_url) else ""
    response = render(request, "portal/recebimento.html", {
        "conta": conta, "form": form, "taxa_texto": TEXTO_TAXA, "taxa_explicacao": EXPLICACAO_TAXA,
        "onboarding_url": onboarding_url,
        "cadastro_url": "https://app.woovi-sandbox.com/register" if (
            conta.api_base_url if conta else settings.WOOVI_ACADEMIAS_BASE_URL
        ).rstrip("/") == "https://api.woovi-sandbox.com" else "https://app.woovi.com/register",
        "anteriores": ContaRecebimento.objects.filter(
            academia=request.academia, modelo_recebimento=ContaRecebimento.LEGADO_SUBCONTA,
        ),
        "ultima_transferencia": Repasse.objects.filter(
            academia=request.academia, status=Repasse.CONCLUIDA, valor__gt=0,
        ).order_by("-concluido_em").first(),
        **_contexto_passos(conta, onboarding_url),
    })
    # Formulários HTTPS precisam preservar a origem para a validação CSRF.
    # Referências continuam omitidas nas navegações para outros sites.
    response["Referrer-Policy"] = "same-origin"
    return response
