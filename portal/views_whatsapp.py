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


@academia_required
@require_http_methods(["GET", "POST"])
def whatsapp_config(request):
    config = _config_da_academia(request.academia)

    if request.method == "POST":
        _exige_admin(request)
        provider = request.POST.get("provider", config.provider)
        if provider in dict(IntegracaoWhatsApp.PROVIDERS):
            config.provider = provider
        config.evolution_base_url = request.POST.get("evolution_base_url", "").strip()
        config.evolution_instance_name = request.POST.get(
            "evolution_instance_name", ""
        ).strip()
        config.credencial_ref = request.POST.get("credencial_ref", "").strip()
        config.n8n_webhook_url = request.POST.get("n8n_webhook_url", "").strip()
        numero_avisos = "".join(c for c in request.POST.get("numero_avisos", "") if c.isdigit())
        if numero_avisos and not 10 <= len(numero_avisos) <= 13:
            messages.error(request, "Informe o WhatsApp para avisos com DDD.")
            return redirect("portal:whatsapp_config")
        config.numero_avisos = numero_avisos
        config.save()
        messages.success(request, "Configuração de WhatsApp salva.")
        return redirect("portal:whatsapp_config")

    return render(
        request,
        "portal/whatsapp_config.html",
        {"config": config, "providers": IntegracaoWhatsApp.PROVIDERS},
    )


@academia_required
@require_http_methods(["POST"])
def whatsapp_criar(request):
    _exige_admin(request)
    try:
        evolution.criar_instancia(request.academia)
    except _ERROS_EVOLUTION as exc:
        messages.error(request, f"Não foi possível criar a conexão: {exc}")
    else:
        messages.success(
            request, "Conexão criada. Gere o QR Code para parear o número."
        )
    return redirect("portal:whatsapp_config")


@academia_required
@require_http_methods(["POST"])
def whatsapp_qrcode(request):
    _exige_admin(request)
    qrcode = None
    try:
        qrcode = evolution.gerar_qrcode(request.academia)
    except _ERROS_EVOLUTION as exc:
        messages.error(request, f"Não foi possível gerar o QR Code: {exc}")

    config = _config_da_academia(request.academia)
    return render(
        request,
        "portal/whatsapp_config.html",
        {
            "config": config,
            "providers": IntegracaoWhatsApp.PROVIDERS,
            "qrcode": qrcode,  # só nesta resposta — nunca gravado no banco
        },
    )


@academia_required
@require_http_methods(["POST"])
def whatsapp_status(request):
    _exige_admin(request)
    try:
        resultado = evolution.consultar_status(request.academia)
    except _ERROS_EVOLUTION as exc:
        messages.error(request, f"Não foi possível verificar o status: {exc}")
    else:
        messages.info(
            request,
            f"Status da conexão: {resultado['status'].replace('_', ' ')}.",
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


@academia_required
@require_http_methods(["POST"])
def whatsapp_numero(request):
    _exige_admin(request)
    novo_numero = request.POST.get("numero_whatsapp", "")
    try:
        evolution.trocar_numero(request.academia, novo_numero)
    except _ERROS_EVOLUTION as exc:
        messages.error(request, f"Não foi possível trocar o número: {exc}")
    else:
        messages.success(
            request,
            "Número atualizado. Gere um novo QR Code para parear este número.",
        )
    return redirect("portal:whatsapp_config")
