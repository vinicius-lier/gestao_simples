from datetime import date, timedelta
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


def redirecionamento_seguro(request, padrao):
    """Volta para a tela de onde a ação foi disparada (campo 'proximo'),
    aceitando só caminhos internos — nunca uma URL externa."""
    proximo = request.POST.get('proximo', '')
    if proximo.startswith('/'):
        return redirect(proximo)
    return redirect(padrao)


def academia_required(view):
    @login_required
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        acesso = AcessoAcademia.objects.select_related('academia').filter(usuario=request.user, academia__ativo=True).first()
        if not acesso:
            raise PermissionDenied('Solicite ao administrador o vínculo a uma academia ativa.')
        request.academia = acesso.academia
        request.administrador_academia = request.user.is_superuser or acesso.administrador
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
    matriculas = aluno.matriculas.filter(academia=request.academia, modalidade__academia=request.academia)
    # Do not expose legacy cross-tenant relationships.
    matriculas = [m for m in matriculas if not m.turma_id or m.turma.academia_id == request.academia.pk]
    for matricula in matriculas:
        matricula.mensalidades_recentes = matricula.mensalidades.filter(academia=request.academia).order_by('-competencia')[:6]
    return render(request, 'portal/detalhe.html', {'aluno': aluno, 'responsavel': responsavel, 'matriculas': matriculas})


@academia_required
@require_http_methods(['GET', 'POST'])
def aluno_form(request, pk=None, matricula_pk=None, nova_matricula=False):
    if nova_matricula and not request.administrador_academia:
        raise PermissionDenied("Somente o administrador autoriza uma nova matrícula para um aluno existente.")
    aluno = get_object_or_404(Atleta, pk=pk, academia=request.academia) if pk else None
    matricula = get_object_or_404(Matricula, pk=matricula_pk, academia=request.academia, atleta=aluno) if matricula_pk else None
    unidade_anterior = matricula.unidade_id if matricula else None
    data = request.POST if request.method == 'POST' else None
    form = AlunoForm(data, instance=aluno, academia=request.academia)
    incluir_matricula = not pk or matricula_pk is not None or nova_matricula
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
                        if not request.administrador_academia and matricula_pk and inscricao.unidade_id != unidade_anterior:
                            raise ValidationError('Somente o administrador pode transferir a matrícula para outro polo.')
                        if not request.administrador_academia and inscricao.unidade_id:
                            outras = aluno.matriculas.exclude(pk=inscricao.pk).exclude(unidade_id=inscricao.unidade_id)
                            if outras.exists():
                                raise ValidationError('Somente o administrador pode autorizar matrícula em outro polo.')
                        inscricao.save()
            except ValidationError as error:
                form.add_error(None, ValidationError(error.messages))
            else:
                messages.success(request, 'Cadastro salvo com sucesso.')
                return redirect('portal:detalhe', pk=aluno.pk)
    return render(request, 'portal/form.html', {'form': form, 'matricula_form': matricula_form, 'aluno': aluno})


def pagina_publica(request):
    from .models import PaginaPublica, FotoPublica
    return render(request, 'portal/publica.html', {
        'pagina': PaginaPublica.objects.order_by('pk').first(),
        'fotos': FotoPublica.objects.filter(publicada=True),
    })


@academia_required
@require_http_methods(['GET', 'POST'])
def cadastros(request, tipo, pk=None, novo=False, excluir=False, detalhe=False):
    from django.db.models import ProtectedError
    from academias.models import Unidade
    from modalidades.models import Professor, Turma, Modalidade
    from .forms import UnidadeForm, ProfessorForm, TurmaForm, ModalidadeForm, GraduacaoFormSet
    cadastro = {
        'unidades': (Unidade, UnidadeForm, 'Unidades / polos', 'unidade'),
        'professores': (Professor, ProfessorForm, 'Professores', 'professor'),
        'turmas': (Turma, TurmaForm, 'Turmas', 'turma'),
        'modalidades': (Modalidade, ModalidadeForm, 'Modalidades', 'modalidade'),
    }
    prefetch = {
        'unidades': ('turmas__modalidade', 'turmas__docente'),
        'professores': ('turmas__modalidade', 'turmas__unidade'),
        'turmas': (),
        'modalidades': ('graduacoes', 'turmas__unidade', 'turmas__docente'),
    }[tipo]
    model, form_class, titulo, singular = cadastro[tipo]
    objetos = model.objects.filter(academia=request.academia).prefetch_related(*prefetch).order_by('nome')
    if not novo and pk is None:
        return render(request, 'portal/cadastros.html', {'objetos': objetos, 'tipo': tipo, 'titulo': titulo})
    if detalhe:
        obj = get_object_or_404(objetos, pk=pk)
        return render(request, 'portal/cadastro_detalhe.html', {'obj': obj, 'tipo': tipo, 'titulo': titulo, 'singular': singular})
    if not request.administrador_academia:
        raise PermissionDenied('Somente o administrador da academia pode alterar estes cadastros.')
    instance = get_object_or_404(objetos, pk=pk) if pk else None
    if excluir:
        if request.method == 'POST':
            try:
                with transaction.atomic():
                    instance.delete()
                messages.success(request, f'{instance.nome}: cadastro excluído.')
            except ProtectedError:
                messages.error(request, f'{instance.nome} não pode ser excluído: há turmas, matrículas ou faixas vinculadas.')
        return redirect('portal:' + tipo)
    data = request.POST if request.method == 'POST' else None
    form = form_class(data, instance=instance, academia=request.academia)
    faixas = None
    if tipo == 'modalidades':
        faixas = GraduacaoFormSet(data, instance=instance or Modalidade(), prefix='faixa', academia=request.academia)
    valido = form.is_valid()
    if faixas is not None:
        valido = faixas.is_valid() and valido
    if request.method == 'POST' and valido:
        with transaction.atomic():
            obj = form.save()
            if faixas is not None:
                faixas.instance = obj
                faixas.save()
        messages.success(request, f'{obj.nome}: cadastro salvo.')
        return redirect('portal:' + tipo)
    return render(request, 'portal/cadastro_form.html', {
        'form': form, 'faixas': faixas, 'tipo': tipo, 'titulo': titulo, 'singular': singular, 'objeto': instance,
    })


@academia_required
def financeiro_dashboard(request):
    from financeiro.models import Mensalidade
    from financeiro.services import resumo_financeiro
    hoje = date.today()
    competencia = date(hoje.year, hoje.month, 1)
    Mensalidade.objects.filter(academia=request.academia).marcar_vencidas()
    base = Mensalidade.objects.filter(academia=request.academia).select_related('matricula__atleta')
    return render(request, 'portal/financeiro_dashboard.html', {
        'competencia': competencia,
        'resumo': resumo_financeiro(request.academia, competencia),
        'proximos_vencimentos': base.filter(status='pendente', vencimento__range=(hoje, hoje + timedelta(days=7))).order_by('vencimento')[:8],
        'atrasadas': base.filter(status='vencida').order_by('vencimento')[:8],
    })


@academia_required
def financeiro_cobrancas(request):
    from financeiro.models import Mensalidade
    Mensalidade.objects.filter(academia=request.academia).marcar_vencidas()
    query = request.GET.get('q', '').strip()
    status = request.GET.get('status', '')
    if status not in dict(Mensalidade.STATUS):
        status = ''
    mes = request.GET.get('mes', '')
    cobrancas = Mensalidade.objects.filter(academia=request.academia).select_related('matricula__atleta').order_by('-vencimento', '-pk')
    if query:
        cobrancas = cobrancas.filter(matricula__atleta__nome__icontains=query)
    if status:
        cobrancas = cobrancas.filter(status=status)
    if mes:
        try:
            ano_filtro, mes_filtro = (int(parte) for parte in mes.split('-', 1))
            cobrancas = cobrancas.filter(competencia__year=ano_filtro, competencia__month=mes_filtro)
        except ValueError:
            mes = ''
    return render(request, 'portal/financeiro_cobrancas.html', {
        'page_obj': Paginator(cobrancas, 25).get_page(request.GET.get('page')),
        'q': query, 'status': status, 'mes': mes, 'status_choices': Mensalidade.STATUS,
    })


@academia_required
@require_http_methods(['POST'])
def financeiro_marcar_pago(request, pk):
    from financeiro.models import Mensalidade
    from financeiro.services import registrar_pagamento
    mensalidade = get_object_or_404(Mensalidade, pk=pk, academia=request.academia)
    forma = request.POST.get('forma_pagamento', '')
    if forma not in dict(Mensalidade.FORMAS_PAGAMENTO):
        forma = ''
    try:
        registrar_pagamento(mensalidade, forma_pagamento=forma)
    except ValueError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, f'Mensalidade de {mensalidade.matricula.atleta.nome} marcada como paga.')
    return redirecionamento_seguro(request, 'portal:financeiro_cobrancas')


@academia_required
@require_http_methods(['POST'])
def financeiro_gerar_pix(request, pk):
    from financeiro.models import Mensalidade
    from integracoes.asaas.client import AsaasAPIError
    from integracoes.asaas.services import criar_cobranca_asaas
    mensalidade = get_object_or_404(Mensalidade, pk=pk, academia=request.academia)
    try:
        criar_cobranca_asaas(mensalidade)
    except (ValueError, AsaasAPIError) as error:
        messages.error(request, f'Não foi possível gerar a cobrança Pix: {error}')
    else:
        messages.success(request, 'Cobrança Pix gerada no Asaas.')
    return redirecionamento_seguro(request, 'portal:financeiro_cobrancas')


@academia_required
def financeiro_pix_qrcode(request, pk):
    """Tela com o QR code e o código copia-e-cola da cobrança já gerada.
    Abre como página normal (funciona sem JS) e também é usada como
    conteúdo do modal de detalhe (mesmo mecanismo de [data-detalhe])."""
    from financeiro.models import Mensalidade
    from integracoes.asaas.client import AsaasAPIError
    from integracoes.asaas.services import obter_pix_mensalidade
    mensalidade = get_object_or_404(
        Mensalidade.objects.select_related('matricula__atleta'), pk=pk, academia=request.academia
    )
    pix, erro = None, None
    if not mensalidade.asaas_payment_id:
        erro = 'Esta mensalidade ainda não tem uma cobrança Pix gerada.'
    else:
        try:
            pix = obter_pix_mensalidade(mensalidade)
        except (ValueError, AsaasAPIError) as error:
            erro = str(error)
    return render(request, 'portal/financeiro_pix.html', {'mensalidade': mensalidade, 'pix': pix, 'erro': erro})
