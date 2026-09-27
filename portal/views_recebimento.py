"""Configurações → Recebimento: a chave Pix onde a academia recebe.

A tela fala só a língua da academia (chave Pix, transferências para a
conta). Nada de provedor, subconta, saldo ou saque — isso é interno.
"""
import logging

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from financeiro.models import ContaRecebimento, Repasse
from integracoes.woovi.chave_pix import ChavePixInvalida
from integracoes.woovi.exceptions import WooviError
from integracoes.woovi.services import RecebimentoBloqueado, configurar_chave_pix

from .forms import ChavePixForm
from .views import academia_required

logger = logging.getLogger(__name__)


def situacao_recebimento(conta):
    """(nível, texto) do status simples mostrado à academia."""
    if conta is None:
        return (
            'pendente',
            'Cadastre a chave Pix onde a escola vai receber as mensalidades. '
            'Sem ela, não é possível gerar Pix para as famílias.',
        )
    ultimo = conta.repasses.order_by('-criado_em', '-pk').first()
    if conta.saque_bloqueado or (ultimo and ultimo.status == Repasse.REQUER_ATENCAO):
        return (
            'atencao',
            'Não conseguimos transferir valores para esta chave Pix. Nossa equipe já foi avisada. '
            'Se a chave mudou, cadastre a nova abaixo.',
        )
    if ultimo and ultimo.status in Repasse.STATUS_ABERTOS:
        return ('andamento', 'Há uma transferência para a sua conta em andamento.')
    return ('ok', 'Recebimento ativo: cada mensalidade paga por Pix é transferida para esta chave.')


@academia_required
@require_http_methods(['GET', 'POST'])
def recebimento(request):
    if not request.administrador_academia:
        raise PermissionDenied('Somente o administrador da academia gerencia o recebimento.')

    conta = ContaRecebimento.ativa_da(request.academia)
    form = ChavePixForm(request.POST or None)

    if request.method == 'POST' and form.is_valid():
        try:
            configurar_chave_pix(
                request.academia, form.cleaned_data['tipo_chave'], form.cleaned_data['chave'], request.user,
            )
        except ChavePixInvalida as erro:
            form.add_error('chave', str(erro))
        except RecebimentoBloqueado as erro:
            messages.error(request, str(erro))
        except WooviError as erro:
            logger.warning('Recebimento: falha ao validar a chave Pix da academia %s: %s', request.academia.pk, erro)
            messages.error(
                request,
                'Não foi possível validar esta chave Pix agora. Confira a chave e tente de novo em alguns minutos.',
            )
        else:
            messages.success(request, 'Chave Pix de recebimento salva.')
            return redirect('portal:recebimento')

    nivel, texto = situacao_recebimento(conta)
    return render(request, 'portal/recebimento.html', {
        'conta': conta,
        'form': form,
        'nivel': nivel,
        'situacao': texto,
        'ultima_transferencia': Repasse.objects.filter(
            academia=request.academia, status=Repasse.CONCLUIDA, valor__gt=0,
        ).order_by('-concluido_em').first(),
        'anteriores': ContaRecebimento.objects.filter(academia=request.academia, ativa=False),
    })
