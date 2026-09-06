from functools import wraps
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods
from atletas.models import Atleta
from matriculas.models import Matricula
from .forms import AlunoForm, MatriculaForm
from .models import AcessoAcademia


def academia_required(view):
    @login_required
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        acesso = AcessoAcademia.objects.select_related('academia').filter(usuario=request.user, academia__ativo=True).first()
        if not acesso:
            raise PermissionDenied('Solicite ao administrador o vínculo a uma academia ativa.')
        request.academia = acesso.academia
        return view(request, *args, **kwargs)
    return wrapped


@academia_required
def dashboard(request):
    return render(request, 'portal/dashboard.html', {
        'total': Atleta.objects.filter(academia=request.academia).count(),
        'ativos': Atleta.objects.filter(academia=request.academia, status='ativo').count(),
        'matriculas': Matricula.objects.filter(academia=request.academia, atleta__academia=request.academia, ativo=True).count(),
        'recentes': Atleta.objects.filter(academia=request.academia).order_by('-criado_em', '-pk')[:5],
    })


@academia_required
def alunos(request):
    query = request.GET.get('q', '').strip()
    status = request.GET.get('status', '')
    if status not in dict(Atleta.STATUS):
        status = ''
    alunos = Atleta.objects.filter(academia=request.academia).order_by('nome', 'pk')
    if query:
        alunos = alunos.filter(nome__icontains=query)
    if status:
        alunos = alunos.filter(status=status)
    return render(request, 'portal/alunos.html', {'page_obj': Paginator(alunos, 20).get_page(request.GET.get('page')), 'q': query, 'status': status})


@academia_required
def detalhe(request, pk):
    aluno = get_object_or_404(Atleta, pk=pk, academia=request.academia)
    responsavel = aluno.responsavel_financeiro
    if responsavel and responsavel.academia_id != request.academia.pk:
        responsavel = None
    matriculas = aluno.matriculas.filter(academia=request.academia, servico__academia=request.academia)
    # Do not expose legacy cross-tenant relationships.
    matriculas = [m for m in matriculas if not m.turma_id or m.turma.academia_id == request.academia.pk]
    return render(request, 'portal/detalhe.html', {'aluno': aluno, 'responsavel': responsavel, 'matriculas': matriculas})


@academia_required
@require_http_methods(['GET', 'POST'])
def aluno_form(request, pk=None, matricula_pk=None):
    aluno = get_object_or_404(Atleta, pk=pk, academia=request.academia) if pk else None
    matricula = get_object_or_404(Matricula, pk=matricula_pk, academia=request.academia, atleta=aluno) if matricula_pk else None
    data = request.POST if request.method == 'POST' else None
    form = AlunoForm(data, instance=aluno, academia=request.academia)
    incluir_matricula = not pk or matricula_pk is not None
    matricula_form = MatriculaForm(data, prefix='matricula', instance=matricula, academia=request.academia) if incluir_matricula else None
    if request.method == 'POST':
        valid = form.is_valid()
        if matricula_form is not None:
            valid = matricula_form.is_valid() and valid
        if valid:
            try:
                with transaction.atomic():
                    aluno = form.save()
                    if matricula_form is not None:
                        inscricao = matricula_form.save(commit=False)
                        inscricao.atleta = aluno
                        inscricao.save()
            except ValidationError as error:
                form.add_error(None, ValidationError(error.messages))
            else:
                messages.success(request, 'Cadastro salvo com sucesso.')
                return redirect('portal:detalhe', pk=aluno.pk)
    return render(request, 'portal/form.html', {'form': form, 'matricula_form': matricula_form, 'aluno': aluno})
