"""Portal do responsável financeiro: área pública (sem o login de staff)
onde ele acompanha as mensalidades dos alunos e paga por Pix, boleto ou
cartão. Acesso por link de uso único (sem senha) — ver TokenAcessoResponsavel.
"""
import re
from functools import wraps

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from atletas.models import Atleta, Responsavel
from financeiro.models import Mensalidade
from .models import TokenAcessoResponsavel

_PADRAO_DESTINO_PAGAR = re.compile(r'^/responsavel/mensalidade/(\d+)/pagar/$')


def destino_pos_login(proximo, responsavel):
    """Só aceita redirecionar, após o login por link, para a página de
    pagamento de uma mensalidade que realmente pertence a esse
    responsável — nunca uma URL externa nem de outra família."""
    correspondencia = _PADRAO_DESTINO_PAGAR.match(proximo or '')
    if not correspondencia:
        return None
    existe = Mensalidade.objects.filter(
        pk=correspondencia.group(1), matricula__atleta__responsavel_financeiro=responsavel
    ).exists()
    return proximo if existe else None


def responsavel_required(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        responsavel = Responsavel.objects.filter(
            pk=request.session.get('responsavel_id'), academia__ativo=True
        ).select_related('academia').first()
        if not responsavel:
            request.session.pop('responsavel_id', None)
            return redirect('portal:responsavel_link_expirado')
        request.responsavel = responsavel
        return view(request, *args, **kwargs)
    return wrapped


@require_http_methods(['GET', 'POST'])
def responsavel_entrar(request, token):
    """GET mostra a tela "Entrar" sem gastar o token; só o POST (clique do
    responsável) consome. Robôs de prévia de link e antivírus fazem GET,
    então não queimam o link de uso único antes da família clicar."""
    acesso = get_object_or_404(
        TokenAcessoResponsavel.objects.select_related('responsavel__academia'), token=token
    )

    def seguir():
        destino = destino_pos_login(request.GET.get('next', ''), acesso.responsavel)
        return redirect(destino) if destino else redirect('portal:responsavel_painel')

    # Já entrou com este responsável neste navegador (ex.: abriu o mesmo
    # link de novo pelo WhatsApp): segue direto, mesmo com o token gasto.
    if request.session.get('responsavel_id') == acesso.responsavel_id:
        return seguir()

    if not acesso.valido:
        return render(request, 'portal/responsavel_link_expirado.html', status=410)

    if request.method == 'GET':
        return render(request, 'portal/responsavel_entrar.html', {'academia': acesso.responsavel.academia})

    acesso.consumir()
    request.session.cycle_key()
    request.session['responsavel_id'] = acesso.responsavel_id
    request.session.set_expiry(60 * 60 * 24 * 14)  # 14 dias, como o padrão de sessão do Django
    messages.success(request, f'Bem-vindo(a), {acesso.responsavel.nome}.')
    return seguir()


def responsavel_link_expirado(request):
    return render(request, 'portal/responsavel_link_expirado.html')


@responsavel_required
@require_http_methods(['POST'])
def responsavel_sair(request):
    request.session.pop('responsavel_id', None)
    return redirect('portal:responsavel_link_expirado')


@responsavel_required
def responsavel_painel(request):
    Mensalidade.objects.filter(academia_id=request.responsavel.academia_id).marcar_vencidas()
    alunos = Atleta.objects.filter(
        responsavel_financeiro=request.responsavel, academia_id=request.responsavel.academia_id
    )
    linhas = [
        {
            'aluno': aluno,
            'mensalidades': Mensalidade.objects.filter(
                matricula__atleta=aluno, academia_id=request.responsavel.academia_id
            ).select_related('matricula__modalidade').order_by('-competencia')[:12],
        }
        for aluno in alunos
    ]
    return render(request, 'portal/responsavel_painel.html', {'linhas': linhas})


@responsavel_required
def responsavel_pagar(request, pk):
    from integracoes.asaas.client import AsaasAPIError
    from integracoes.asaas.services import criar_cobranca_multipla_asaas, obter_pix_mensalidade

    mensalidade = get_object_or_404(
        Mensalidade.objects.select_related('matricula__atleta', 'matricula__modalidade', 'matricula__unidade'),
        pk=pk,
        academia_id=request.responsavel.academia_id,
        matricula__atleta__responsavel_financeiro=request.responsavel,
    )

    pix, erro = None, None
    if mensalidade.status in ('pendente', 'vencida'):
        try:
            if not mensalidade.asaas_payment_id:
                criar_cobranca_multipla_asaas(mensalidade)
            pix = obter_pix_mensalidade(mensalidade)
        except (ValueError, AsaasAPIError) as error:
            erro = str(error)

    return render(request, 'portal/responsavel_pagar.html', {
        'mensalidade': mensalidade, 'pix': pix, 'erro': erro,
    })
