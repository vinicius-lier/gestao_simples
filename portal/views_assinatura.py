"""Configurações → Minha assinatura: a mensalidade que a academia paga pelo
sistema. Só o administrador da academia vê."""
from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from assinaturas.models import Assinatura, FaturaAssinatura
from assinaturas.services import informar_pagamento, pix_da_fatura

from .views import academia_required


def _exige_admin(request):
    if not request.administrador_academia:
        raise PermissionDenied("Somente o administrador da academia vê a assinatura do sistema.")


@academia_required
def minha_assinatura(request):
    _exige_admin(request)
    assinatura = Assinatura.objects.filter(academia=request.academia).first()
    a_pagar = None
    if assinatura:
        a_pagar = assinatura.faturas.filter(status=FaturaAssinatura.ABERTA).order_by("vencimento").first()
    return render(request, "portal/assinatura.html", {
        "assinatura": assinatura,
        "faturas": assinatura.faturas.all()[:12] if assinatura else [],
        "a_pagar": a_pagar,
        "br_code": pix_da_fatura(a_pagar) if a_pagar else "",
        "recebedor": getattr(settings, "PLATAFORMA_PIX_NOME", ""),
    })


@academia_required
@require_POST
def assinatura_informar_pagamento(request, pk):
    _exige_admin(request)
    fatura = get_object_or_404(FaturaAssinatura, pk=pk, assinatura__academia=request.academia)
    try:
        informar_pagamento(fatura)
    except ValueError as erro:
        messages.error(request, str(erro))
    else:
        messages.success(
            request,
            "Obrigado! Avisamos a equipe do sistema. Assim que o pagamento for conferido, a fatura aparece como paga.",
        )
    return redirect("portal:minha_assinatura")
