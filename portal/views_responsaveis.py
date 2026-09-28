"""Alunos → Responsáveis: quem paga as mensalidades, com os dados, os alunos
por quem responde e as cobranças de todos eles. Editar é só do
administrador: o WhatsApp do responsável é para onde vão as cobranças."""
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from atletas.models import Responsavel
from financeiro.models import Mensalidade

from .forms import ResponsavelForm, digits
from .models import TokenAcessoResponsavel
from .views import academia_required


def _iniciais(nome):
    partes = nome.split()
    return ''.join(p[0] for p in (partes[:1] + partes[1:][-1:])).upper() or '?'


@academia_required
def responsaveis(request):
    query = request.GET.get('q', '').strip()
    lista = Responsavel.objects.filter(academia=request.academia).prefetch_related('atletas').order_by('nome', 'pk')
    if query:
        numeros = digits(query)
        filtro = Q(nome__icontains=query) | Q(email__icontains=query)
        if numeros:
            filtro |= Q(cpf__contains=numeros) | Q(whatsapp__contains=numeros)
        lista = lista.filter(filtro)
    pagina = Paginator(lista, 25).get_page(request.GET.get('page'))
    em_aberto = dict(
        Mensalidade.objects.filter(
            academia=request.academia, status__in=('pendente', 'vencida'),
            matricula__atleta__responsavel_financeiro__in=[r.pk for r in pagina],
        ).values_list('matricula__atleta__responsavel_financeiro').annotate(total=Sum('valor'))
    )
    for responsavel in pagina:
        responsavel.em_aberto = em_aberto.get(responsavel.pk, 0)
    return render(request, 'portal/responsaveis.html', {'page_obj': pagina, 'q': query})


@academia_required
def responsavel_detalhe(request, pk):
    responsavel = get_object_or_404(Responsavel, pk=pk, academia=request.academia)
    alunos = list(
        responsavel.atletas.filter(academia=request.academia)
        .prefetch_related('matriculas__turma', 'matriculas__modalidade').order_by('nome')
    )
    for aluno in alunos:
        # Cada turma uma vez, mesmo com duas matrículas ativas nela.
        aluno.turmas_ativas = sorted({str(m.turma or m.modalidade) for m in aluno.matriculas.all() if m.ativo})
    cobrancas = Mensalidade.objects.filter(
        academia=request.academia, matricula__atleta__responsavel_financeiro=responsavel,
    )
    cobrancas.marcar_vencidas()
    hoje = timezone.localdate()
    em_aberto = list(cobrancas.em_aberto().order_by('vencimento'))
    return render(request, 'portal/responsavel_detalhe.html', {
        'responsavel': responsavel,
        'iniciais': _iniciais(responsavel.nome),
        'alunos': alunos,
        'cobrancas_recentes': cobrancas.select_related('matricula__atleta').order_by('-vencimento', '-pk')[:12],
        'total_cobrancas': cobrancas.count(),
        'em_aberto': sum((m.valor_devido(hoje) for m in em_aberto), start=0),
        'atrasado': sum((m.valor_devido(hoje) for m in em_aberto if m.vencimento < hoje), start=0),
        'proxima': next((m for m in em_aberto if m.vencimento >= hoje), None),
    })


@academia_required
@require_http_methods(['GET', 'POST'])
def responsavel_editar(request, pk):
    if not request.administrador_academia:
        raise PermissionDenied('Somente o administrador da academia altera os dados do responsável.')
    responsavel = get_object_or_404(Responsavel, pk=pk, academia=request.academia)
    form = ResponsavelForm(request.POST or None, instance=responsavel)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, f'{responsavel.nome}: dados salvos.')
        return redirect('portal:responsavel_detalhe', pk=responsavel.pk)
    return render(request, 'portal/responsavel_form.html', {'form': form, 'responsavel': responsavel})


@academia_required
@require_POST
def responsavel_enviar_acesso(request, pk):
    """Manda o link de acesso ao portal da família (o 1º acesso leva a criar
    a senha). Sem WhatsApp configurado, mostra o link para enviar à mão."""
    from integracoes.whatsapp import enviar_acesso
    from integracoes.whatsapp.base import WhatsAppProviderError

    responsavel = get_object_or_404(Responsavel, pk=pk, academia=request.academia)
    acesso = TokenAcessoResponsavel.gerar(responsavel)
    link = request.build_absolute_uri(reverse('portal:responsavel_entrar', args=[acesso.token]))
    try:
        enviar_acesso(request.academia, responsavel, link)
    except ValueError:
        pass  # WhatsApp não configurado: mostra o link abaixo
    except WhatsAppProviderError as error:
        messages.warning(request, f'Não deu para enviar automaticamente pelo WhatsApp: {error}')
    else:
        messages.success(request, f'Acesso enviado para {responsavel.nome} pelo WhatsApp.')
        return redirect('portal:responsavel_detalhe', pk=responsavel.pk)
    mensagem = f'Olá, {responsavel.nome}! Aqui está o link para acompanhar as mensalidades e pagar: {link}'
    return render(request, 'portal/acesso_responsavel_gerado.html', {
        'aluno': None, 'responsavel': responsavel, 'link': link, 'mensagem': mensagem,
    })
