"""Painel: Configurações → WhatsApp (Evolution API).

Todas as views passam por ``academia_required`` (login + academia ativa) e as
ações de escrita exigem administrador da academia. O tenant é sempre
``request.academia`` — nunca um id vindo da requisição —, então um usuário de
uma academia não consegue mexer na instância de outra.
"""
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from academias.models import IntegracaoWhatsApp
from integracoes.evolution import services as evolution
from integracoes.evolution.client import EvolutionAPIError, EvolutionConfigError
from portal.views import academia_required

_ERROS_EVOLUTION = (EvolutionAPIError, EvolutionConfigError)


def _config_da_academia(academia):
    config, _ = IntegracaoWhatsApp.objects.get_or_create(academia=academia)
    return config


def _exige_admin(request):
    if not request.administrador_academia:
        raise PermissionDenied(
            "Somente o administrador da academia configura o WhatsApp."
        )


def _tela(request, config, **extra):
    return render(request, "portal/whatsapp_config.html", {"config": config, **extra})


@academia_required
@require_http_methods(["GET", "POST"])
def whatsapp_config(request):
    """A academia só vê o número, a conexão e o WhatsApp de avisos. URL,
    chave e instância da Evolution são da plataforma (settings/admin) — o
    POST daqui não os altera."""
    config = _config_da_academia(request.academia)

    if request.method == "POST":
        _exige_admin(request)
        numero_avisos = "".join(c for c in request.POST.get("numero_avisos", "") if c.isdigit())
        if numero_avisos and not 10 <= len(numero_avisos) <= 13:
            messages.error(request, "Informe o WhatsApp para avisos com DDD.")
            return redirect("portal:whatsapp_config")
        config.numero_avisos = numero_avisos
        config.save(update_fields=["numero_avisos", "atualizado_em"])
        messages.success(request, "WhatsApp para avisos salvo.")
        return redirect("portal:whatsapp_config")

    return _tela(request, config)


@academia_required
@require_http_methods(["POST"])
def whatsapp_qrcode(request):
    """Número → QR Code: cria a conexão na primeira vez, troca o número se
    ele mudou e devolve o QR para ler com o celular."""
    _exige_admin(request)
    config = _config_da_academia(request.academia)
    numero = request.POST.get("numero", "") or config.numero_whatsapp
    try:
        resultado = evolution.conectar(request.academia, numero)
    except _ERROS_EVOLUTION as exc:
        messages.error(request, f"Não foi possível gerar o QR Code: {exc}")
        return redirect("portal:whatsapp_config")

    if resultado.get("conectado"):
        messages.success(request, "Este WhatsApp já está conectado.")
        return redirect("portal:whatsapp_config")
    config.refresh_from_db()
    # O QR vai só nesta resposta — nunca é gravado no banco.
    return _tela(request, config, qrcode=resultado)


@academia_required
@require_http_methods(["POST"])
def whatsapp_status(request):
    _exige_admin(request)
    try:
        resultado = evolution.consultar_status(request.academia)
    except _ERROS_EVOLUTION as exc:
        messages.error(request, f"Não foi possível verificar a conexão: {exc}")
    else:
        if resultado["status"] == IntegracaoWhatsApp.STATUS_CONECTADO:
            messages.success(request, "WhatsApp conectado. Os lembretes já saem por este número.")
        else:
            situacao = dict(IntegracaoWhatsApp.STATUS_CONEXAO)[resultado["status"]].lower()
            messages.info(
                request,
                f"O WhatsApp ainda não está conectado ({situacao}). Gere o QR Code e leia com o celular.",
            )
    return redirect("portal:whatsapp_config")


@academia_required
@require_http_methods(["POST"])
def whatsapp_desconectar(request):
    _exige_admin(request)
    try:
        evolution.desconectar(request.academia)
    except _ERROS_EVOLUTION as exc:
        messages.error(request, f"Não foi possível desconectar: {exc}")
    else:
        messages.success(request, "Conexão encerrada.")
    return redirect("portal:whatsapp_config")
