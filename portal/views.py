from datetime import date, timedelta
from functools import wraps
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods
from atletas.models import Atleta
from matriculas.models import Matricula
from .forms import AlunoForm, MatriculaForm
from .models import AcessoAcademia, TokenAcessoResponsavel


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
                        if inscricao.ativo:
                            from financeiro.services import gerar_mensalidade_inicial
                            gerar_mensalidade_inicial(inscricao)
            except ValidationError as error:
                form.add_error(None, ValidationError(error.messages))
            else:
                messages.success(request, 'Cadastro salvo com sucesso.')
                return redirect('portal:detalhe', pk=aluno.pk)
    return render(request, 'portal/form.html', {'form': form, 'matricula_form': matricula_form, 'aluno': aluno})


def pagina_publica(request):
    from .models import PaginaPublica, FotoPublica
    from .views_experimentais import contexto_publico
    return render(request, 'portal/publica.html', {
        **contexto_publico(request),
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
def matricula_ativar(request, pk, matricula_pk):
    """Ativa a matrícula direto do cadastro do aluno — um clique, sem
    passar pelo formulário de edição. Se ela veio de um convite de
    matrícula ainda 'preenchido', ativa pelo mesmo caminho do convite
    (mantém o status do convite em sincronia); senão, liga só o campo."""
    if not request.administrador_academia:
        raise PermissionDenied('Somente o administrador da academia ativa matrículas.')

    from .models import ConviteMatricula
    from .services_matricula import ativar_convite

    matricula = get_object_or_404(
        Matricula, pk=matricula_pk, atleta__pk=pk, academia=request.academia,
    )
    if matricula.ativo:
        messages.info(request, 'Esta matrícula já está ativa.')
        return redirect('portal:detalhe', pk=pk)

    convite = ConviteMatricula.objects.filter(
        matricula=matricula, status=ConviteMatricula.PREENCHIDO,
    ).first()
    if convite is not None:
        ativar_convite(convite)
    else:
        from financeiro.services import gerar_mensalidade_inicial
        matricula.ativo = True
        matricula.save(update_fields=['ativo'])
        gerar_mensalidade_inicial(matricula)
    messages.success(request, 'Matrícula ativada.')
    return redirect('portal:detalhe', pk=pk)


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
def financeiro_encerrar_cobranca(request, pk):
    """Cancela ou isenta uma mensalidade em aberto. Só administrador:
    diferente de dar baixa, isso apaga uma dívida."""
    if not request.administrador_academia:
        raise PermissionDenied('Somente o administrador da academia cancela ou isenta mensalidades.')

    from financeiro.models import Mensalidade
    from financeiro.services import encerrar_mensalidade
    from integracoes.asaas.client import AsaasAPIError

    mensalidade = get_object_or_404(
        Mensalidade.objects.select_related('matricula__atleta'), pk=pk, academia=request.academia
    )
    try:
        encerrar_mensalidade(mensalidade, request.POST.get('status', ''))
    except (ValueError, AsaasAPIError) as error:
        messages.error(request, f'Não foi possível alterar a mensalidade: {error}')
    else:
        messages.success(
            request,
            f'Mensalidade {mensalidade.competencia:%m/%Y} de {mensalidade.matricula.atleta.nome}: '
            f'{mensalidade.get_status_display().lower()}.',
        )
    return redirecionamento_seguro(request, 'portal:financeiro_cobrancas')


@academia_required
@require_http_methods(['POST'])
def financeiro_gerar_pix(request, pk):
    from financeiro.models import Mensalidade
    from integracoes.asaas.client import AsaasAPIError
    from integracoes.asaas.services import criar_cobranca_multipla_asaas
    mensalidade = get_object_or_404(Mensalidade, pk=pk, academia=request.academia)
    try:
        criar_cobranca_multipla_asaas(mensalidade)
    except (ValueError, AsaasAPIError) as error:
        messages.error(request, f'Não foi possível gerar a cobrança: {error}')
    else:
        messages.success(request, 'Cobrança gerada no Asaas (Pix, boleto e cartão).')
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
        Mensalidade.objects.select_related('matricula__atleta', 'matricula__modalidade', 'matricula__unidade'),
        pk=pk, academia=request.academia,
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


@academia_required
@require_http_methods(['POST'])
def gerar_acesso_responsavel(request, pk):
    """Gera um link de acesso ao portal do responsável. Se a API do
    WhatsApp estiver configurada, envia sozinho; senão, mostra o link
    para o operador mandar manualmente (mesmo caminho de sempre)."""
    from integracoes.whatsapp import enviar_acesso
    from integracoes.whatsapp.base import WhatsAppProviderError

    aluno = get_object_or_404(Atleta, pk=pk, academia=request.academia)
    responsavel = aluno.responsavel_financeiro
    if responsavel is None or responsavel.academia_id != request.academia.pk:
        messages.error(request, 'Este aluno não tem responsável financeiro cadastrado.')
        return redirect('portal:detalhe', pk=aluno.pk)

    acesso = TokenAcessoResponsavel.gerar(responsavel)
    link = request.build_absolute_uri(reverse('portal:responsavel_entrar', args=[acesso.token]))

    try:
        enviar_acesso(request.academia, responsavel, link)
    except ValueError:
        pass  # API do WhatsApp não configurada: operador envia manualmente abaixo
    except WhatsAppProviderError as error:
        messages.warning(request, f'Não deu para enviar automaticamente pelo WhatsApp: {error}')
    else:
        messages.success(request, f'Acesso enviado automaticamente para {responsavel.nome} pelo WhatsApp.')
        return redirect('portal:detalhe', pk=aluno.pk)

    mensagem = f'Olá, {responsavel.nome}! Aqui está o link para acompanhar as mensalidades e pagar: {link}'
    return render(request, 'portal/acesso_responsavel_gerado.html', {
        'aluno': aluno, 'responsavel': responsavel, 'link': link, 'mensagem': mensagem,
    })


@academia_required
@require_http_methods(['POST'])
def financeiro_enviar_cobranca(request, pk):
    """Manda a cobrança (valor, vencimento e link de pagamento) direto
    para o responsável pelo WhatsApp. O link já entra logado e cai
    direto na página de pagamento dessa mensalidade — não no painel
    geral. Mesma regra de fallback do acesso ao portal: sem a API do
    WhatsApp configurada, mostra o link para enviar na mão."""
    from financeiro.lembretes import calcular_estagio
    from financeiro.models import Mensalidade
    from integracoes.whatsapp import enviar_cobranca
    from integracoes.whatsapp.base import WhatsAppProviderError

    mensalidade = get_object_or_404(
        Mensalidade.objects.select_related('matricula__atleta'), pk=pk, academia=request.academia
    )
    aluno = mensalidade.matricula.atleta
    responsavel = aluno.responsavel_financeiro
    if responsavel is None or responsavel.academia_id != request.academia.pk:
        messages.error(request, 'Este aluno não tem responsável financeiro cadastrado.')
        return redirecionamento_seguro(request, 'portal:financeiro_cobrancas')

    acesso = TokenAcessoResponsavel.gerar(responsavel, validade_horas=TokenAcessoResponsavel.VALIDADE_PAGAMENTO_HORAS)
    destino = reverse('portal:responsavel_pagar', args=[mensalidade.pk])
    link = request.build_absolute_uri(reverse('portal:responsavel_entrar', args=[acesso.token])) + f'?next={destino}'

    try:
        enviar_cobranca(
            request.academia, responsavel, mensalidade, link,
            estagio=calcular_estagio(mensalidade),
        )
    except ValueError:
        pass  # API do WhatsApp não configurada: operador envia manualmente abaixo
    except WhatsAppProviderError as error:
        messages.warning(request, f'Não deu para enviar automaticamente pelo WhatsApp: {error}')
    else:
        messages.success(request, f'Cobrança enviada automaticamente para {responsavel.nome} pelo WhatsApp.')
        return redirecionamento_seguro(request, 'portal:financeiro_cobrancas')

    mensagem = (
        f'Olá, {responsavel.nome}! A mensalidade de {aluno.nome} referente a '
        f'{mensalidade.competencia:%m/%Y} está no valor de R$ {mensalidade.valor}, com vencimento '
        f'em {mensalidade.vencimento:%d/%m/%Y}. Pague por aqui: {link}'
    )
    return render(request, 'portal/acesso_responsavel_gerado.html', {
        'aluno': aluno, 'responsavel': responsavel, 'link': link, 'mensagem': mensagem,
    })
