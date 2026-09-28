"""Configurações → Minha assinatura: a mensalidade que a academia paga pelo
sistema. Só o administrador da academia vê e paga — inclusive com a
assinatura suspensa (estas telas ficam fora do bloqueio)."""
import logging

from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from assinaturas.models import FaturaAssinatura
from assinaturas.services import conferir_pagamento, fatura_a_pagar, garantir_pix, proximo_vencimento_previsto

from .views import academia_required

logger = logging.getLogger(__name__)

# A tela do Pix pergunta o status a cada poucos segundos; a Woovi só é
# consultada (rede de segurança do webhook) no máximo uma vez neste intervalo.
INTERVALO_CONSULTA_WOOVI = 30


def _exige_admin(request):
    if not request.administrador_academia:
        raise PermissionDenied("Somente o administrador da academia vê a assinatura do sistema.")


@academia_required
def minha_assinatura(request):
    _exige_admin(request)
    # Já com as regras do dia aplicadas (academia_required).
    assinatura = request.assinatura_sistema
    contexto = {'assinatura': assinatura}
    if assinatura:
        a_pagar = fatura_a_pagar(assinatura)
        contexto.update({
            'a_pagar': a_pagar,
            'atrasada': a_pagar is not None and a_pagar.status == FaturaAssinatura.ATRASADA,
            'proximo_vencimento': a_pagar.vencimento if a_pagar else proximo_vencimento_previsto(assinatura),
            'historico': assinatura.faturas.all()[:24],
        })
    return render(request, 'portal/assinatura.html', contexto)


@academia_required
def assinatura_pagar(request):
    """Pagamento da mensalidade do sistema dentro do próprio sistema: a
    fatura em aberto mais próxima, com QR Code e Pix copia e cola."""
    from integracoes.woovi.exceptions import WooviError

    _exige_admin(request)
    assinatura = request.assinatura_sistema
    if assinatura is None:
        return redirect('portal:minha_assinatura')
    fatura = fatura_a_pagar(assinatura)
    if fatura is None:
        return redirect('portal:minha_assinatura')
    erro = None
    try:
        fatura = garantir_pix(fatura)
    except (ValueError, WooviError) as falha:
        logger.warning('Pix da assinatura %s indisponível: %s', fatura.pk, falha)
        erro = 'O pagamento por Pix está indisponível no momento. Tente novamente em alguns minutos.'
    return render(request, 'portal/assinatura_pagar.html', {'assinatura': assinatura, 'fatura': fatura, 'erro': erro})


@academia_required
def assinatura_status(request, pk):
    """Situação da fatura para a tela do Pix se atualizar sozinha. A baixa
    oficial vem do webhook; se ele atrasar, confere na Woovi (no máximo a
    cada ``INTERVALO_CONSULTA_WOOVI`` segundos)."""
    _exige_admin(request)
    fatura = get_object_or_404(FaturaAssinatura, pk=pk, assinatura__academia=request.academia)
    if fatura.em_aberto and fatura.correlation_id and cache.add(
        f'assinatura:conferir:{fatura.pk}', True, INTERVALO_CONSULTA_WOOVI,
    ):
        conferir_pagamento(fatura)
        fatura.refresh_from_db()
    return JsonResponse({'status': fatura.status, 'paga': fatura.status == FaturaAssinatura.PAGA})
