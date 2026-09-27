from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import F
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST
from .forms_experimentais import (
    AulaExperimentalForm,
    ConfiguracaoExperimentalForm,
    InscricaoExperimentalForm,
)
from .models import AulaExperimental, ConfiguracaoExperimental, InscricaoExperimental
from .services_experimentais import inscrever, alterar_status, bloquear_aula
from .views import academia_required


def contexto_publico(request):
    aulas = AulaExperimental.objects.filter(
        ativa=True, fim__gt=timezone.now()
    ).select_related('turma__academia', 'turma__unidade', 'turma__modalidade')
    configs = {c.academia_id: c for c in ConfiguracaoExperimental.objects.all()}

    visiveis = []
    for a in aulas:
        cfg = configs.get(a.turma.academia_id) or ConfiguracaoExperimental(academia=a.turma.academia)
        if a.disponivel_no_site(cfg) and (a.vagas_disponiveis or a.fila_habilitada):
            visiveis.append(a)

    unidades = {a.turma.unidade_id: a.turma.unidade for a in visiveis}
    modalidades = {a.turma.modalidade_id: a.turma.modalidade for a in visiveis}
    unidade = request.GET.get('unidade', '')
    modalidade = request.GET.get('modalidade', '')
    aulas = [a for a in visiveis if (not unidade or str(a.turma.unidade_id) == unidade) and (not modalidade or str(a.turma.modalidade_id) == modalidade)]
    return {'aulas_experimentais': aulas, 'unidades_experimentais': unidades.values(),
            'modalidades_experimentais': modalidades.values(), 'unidade_filtro': unidade, 'modalidade_filtro': modalidade}


@require_http_methods(['GET', 'POST'])
def agendar(request, pk=None):
    if pk is None:
        return render(request, 'portal/experimentais_publico.html', contexto_publico(request))
    aula = get_object_or_404(AulaExperimental, pk=pk, ativa=True, turma__academia__ativo=True,
                             turma__ativo=True, turma__unidade__ativo=True, turma__modalidade__ativo=True,
                             turma__unidade__academia_id=F('turma__academia_id'),
                             turma__modalidade__academia_id=F('turma__academia_id'))
    disponivel = aula.disponivel_no_site()
    form = InscricaoExperimentalForm(request.POST if request.method == 'POST' else None)
    if request.method == 'POST' and form.is_valid():
        dados = dict(form.cleaned_data)
        aceitar = dados.pop('aceitar_fila')
        try:
            inscricao = inscrever(aula.pk, dados, aceitar)
        except ValidationError as error:
            form.add_error(None, error)
        else:
            request.session['experimental_resultado'] = {'status': inscricao.get_status_display(), 'aula': str(aula), 'espera': inscricao.status == 'espera'}
            return redirect('portal:experimental_resultado')
    return render(request, 'portal/experimentais_agendar.html', {'aula': aula, 'form': form, 'disponivel': disponivel})


def resultado(request):
    resultado = request.session.pop('experimental_resultado', None)
    if not resultado:
        return redirect('portal:experimentais_publico')
    return render(request, 'portal/experimentais_resultado.html', {'resultado': resultado})


@academia_required
@require_http_methods(['GET', 'POST'])
def configuracao(request):
    cfg, _ = ConfiguracaoExperimental.objects.get_or_create(academia=request.academia)
    if request.method == 'POST' and not request.administrador_academia:
        raise PermissionDenied('Somente o administrador pode alterar as configurações.')
    form = ConfiguracaoExperimentalForm(request.POST or None, instance=cfg)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Configurações das aulas experimentais salvas.')
        return redirect('portal:experimental_config')
    return render(request, 'portal/experimentais_config.html', {'form': form})


@academia_required
@require_http_methods(['GET', 'POST'])
def agenda(request, pk=None, novo=False):
    aulas = AulaExperimental.objects.filter(turma__academia=request.academia).select_related('turma__unidade', 'turma__modalidade')
    if pk is None and not novo:
        return render(request, 'portal/experimentais_agenda.html', {'aulas': aulas})
    aula = get_object_or_404(aulas, pk=pk) if pk else None
    if not novo and request.GET.get('editar') != '1' and request.method == 'GET':
        return render(request, 'portal/experimentais_detalhe.html', {'aula': aula, 'inscricoes': aula.inscricoes.all()})
    if not request.administrador_academia:
        raise PermissionDenied('Somente o administrador pode configurar a agenda.')
    with transaction.atomic():
        if request.method == 'POST' and aula:
            aula = bloquear_aula(aula.pk)
        form = AulaExperimentalForm(request.POST if request.method == 'POST' else None, instance=aula, academia=request.academia)
        if request.method == 'POST' and form.is_valid():
            salva = form.save()
            messages.success(request, 'Aula experimental salva.')
            return redirect('portal:experimental_detalhe', pk=salva.pk)
    return render(request, 'portal/experimentais_form.html', {'form': form, 'aula': aula})


@academia_required
@require_POST
def status(request, pk):
    inscricao = get_object_or_404(InscricaoExperimental, pk=pk, aula__turma__academia=request.academia)
    try:
        alterar_status(inscricao, request.POST.get('status'))
    except ValidationError as error:
        messages.error(request, ' '.join(error.messages))
    else:
        messages.success(request, 'Inscrição atualizada.')
    return redirect('portal:experimental_detalhe', pk=inscricao.aula_id)
