"""Configuração da conta própria; histórico anterior continua consultável."""
import logging

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from financeiro.models import ContaRecebimento, Repasse
from integracoes.woovi.contas import (
    EXPLICACAO_TAXA, TEXTO_TAXA, ativar_conta, atualizar_onboarding,
    iniciar_onboarding, link_kyc_seguro, preparar_conta,
)
from integracoes.woovi.exceptions import WooviConfigError, WooviError
from .forms import ContaWooviForm
from .views import academia_required

logger = logging.getLogger(__name__)


@academia_required
@never_cache
@require_http_methods(["GET", "POST"])
def recebimento(request):
    if not request.administrador_academia:
        raise PermissionDenied("Somente o administrador da academia gerencia o recebimento.")
    conta = ContaRecebimento.objects.filter(
        academia=request.academia, modelo_recebimento=ContaRecebimento.CONTA_PROPRIA,
    ).first()
    form = ContaWooviForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        acao = request.POST.get("acao")
        if acao not in ("iniciar", "ativar", "atualizar"):
            form.add_error(None, "Escolha uma ação de configuração.")
        else:
            try:
                conta = preparar_conta(request.academia, request.user)
                if acao == "iniciar":
                    if settings.WOOVI_ONBOARDING_APP_ID:
                        iniciar_onboarding(conta)
                        messages.success(request, "Cadastro iniciado. Continue na página segura da Woovi abaixo.")
                    else:
                        messages.info(request, "Abra a conta pelo cadastro oficial abaixo. Depois, solicite ao suporte a conexão segura e volte para ativar.")
                elif acao == "atualizar":
                    atualizar_onboarding(conta)
                    messages.success(request, "Situação do cadastro atualizada.")
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
    response = render(request, "portal/recebimento.html", {
        "conta": conta, "form": form, "taxa_texto": TEXTO_TAXA, "taxa_explicacao": EXPLICACAO_TAXA,
        "onboarding_url": conta.onboarding_url if conta and link_kyc_seguro(conta.onboarding_url) else "",
        "cadastro_url": "https://app.woovi-sandbox.com/register" if (
            conta.api_base_url if conta else settings.WOOVI_ACADEMIAS_BASE_URL
        ).rstrip("/") == "https://api.woovi-sandbox.com" else "https://app.woovi.com/register",
        "anteriores": ContaRecebimento.objects.filter(
            academia=request.academia, modelo_recebimento=ContaRecebimento.LEGADO_SUBCONTA,
        ),
        "ultima_transferencia": Repasse.objects.filter(
            academia=request.academia, status=Repasse.CONCLUIDA, valor__gt=0,
        ).order_by("-concluido_em").first(),
    })
    # Formulários HTTPS precisam preservar a origem para a validação CSRF.
    # Referências continuam omitidas nas navegações para outros sites.
    response["Referrer-Policy"] = "same-origin"
    return response
