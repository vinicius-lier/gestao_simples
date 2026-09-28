import logging
from datetime import date, timedelta
from functools import wraps
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from atletas.models import Atleta
from matriculas.models import Matricula
from .forms import AlunoForm, MatriculaForm, responsavel_divergente
from .models import AcessoAcademia, TokenAcessoResponsavel


logger = logging.getLogger(__name__)


def mensagem_erro_pix(error, acao):
    """Texto de erro de Pix para a equipe da academia: nunca cita o provedor
    de pagamento nem detalhes técnicos (esses vão para o log)."""
    from integracoes.woovi.exceptions import WooviConfigError, WooviError
    from integracoes.woovi.services import RecebimentoNaoConfigurado

    if isinstance(error, RecebimentoNaoConfigurado):
        return 'Cadastre a chave Pix de recebimento em Configurações → Recebimento.'
    if isinstance(error, WooviError):
        logger.warning('Pix: falha ao %s: %s', acao, error)
        if isinstance(error, WooviConfigError):
            return 'O recebimento por Pix está indisponível no momento. Fale com o suporte.'
        return f'Não foi possível {acao} agora. Tente novamente em alguns minutos.'
    return str(error)


def redirecionamento_seguro(request, padrao):
    """Volta para a tela de onde a ação foi disparada (campo 'proximo'),
    aceitando só caminhos internos — nunca uma URL externa."""
    proximo = request.POST.get('proximo', '')
    if proximo.startswith('/'):
        return redirect(proximo)
    return redirect(padrao)


# Com a assinatura do sistema suspensa, só estas telas do painel abrem: o
# administrador vê a cobrança e paga. Nada é apagado.
TELAS_LIBERADAS_NA_SUSPENSAO = {'minha_assinatura', 'assinatura_pagar', 'assinatura_status'}


def academia_required(view):
    @login_required
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        acesso = AcessoAcademia.objects.select_related('academia').filter(usuario=request.user, academia__ativo=True).first()
        if not acesso:
            raise PermissionDenied('Solicite ao administrador o vínculo a uma academia ativa.')
        request.academia = acesso.academia
        request.administrador_academia = request.user.is_superuser or acesso.administrador
        bloqueio = _bloqueio_da_assinatura(request)
        if bloqueio is not None:
            return bloqueio
        return view(request, *args, **kwargs)
    return wrapped


def _bloqueio_da_assinatura(request):
    """Aplica as regras do dia na assinatura (atraso, suspensão) e, se ela
    estiver suspensa, fecha as telas operacionais. A equipe da plataforma
    (superusuário) continua entrando, com o aviso no topo."""
    from assinaturas.services import assinatura_da, atualizar_situacao

    assinatura = assinatura_da(request.academia)
    if assinatura is not None:
        atualizar_situacao(assinatura)
    request.assinatura_sistema = assinatura
    if assinatura is None or not assinatura.suspensa or request.user.is_superuser:
        return None
    tela = request.resolver_match.url_name if request.resolver_match else ''
    if tela in TELAS_LIBERADAS_NA_SUSPENSAO:
        return None
    if request.administrador_academia:
        return redirect('portal:minha_assinatura')
    return render(request, 'portal/assinatura_suspensa.html', status=403)


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
    from financeiro.models import Mensalidade
    from .forms_matricula import AtivacaoMatriculaForm
    for matricula in matriculas:
        if not matricula.ativo and request.administrador_academia:
            matricula.ativacao_form = AtivacaoMatriculaForm(matricula=matricula, prefix=f'ativar-{matricula.pk}')

    cobrancas = Mensalidade.objects.filter(
        academia=request.academia, matricula__in=[m.pk for m in matriculas],
    )
    cobrancas.marcar_vencidas()
    em_aberto = list(cobrancas.em_aberto().order_by('vencimento'))
    hoje = timezone.localdate()
    fichas = list(aluno.fichas_matricula.filter(academia=request.academia))
    aba = request.GET.get('aba') if request.GET.get('aba') in ('cadastro', 'ficha') else 'cadastro'
    partes = aluno.nome.split()
    return render(request, 'portal/detalhe.html', {
        'aluno': aluno,
        'iniciais': ''.join(p[0] for p in (partes[:1] + partes[1:][-1:])).upper() or '?',
        'responsavel': responsavel,
        'responsavel_divergente': responsavel_divergente(aluno) if responsavel else None,
        'matriculas': matriculas,
        'ativas': [m for m in matriculas if m.ativo],
        'fichas': fichas,
        'ficha_recente': fichas[0] if fichas else None,
        'aba': aba,
        'alertas_saude': fichas[0].alertas_saude if fichas else [],
        'idade': _idade(aluno.data_nascimento, hoje),
        'cobrancas_recentes': cobrancas.select_related('matricula__modalidade').order_by('-vencimento', '-pk')[:8],
        'total_cobrancas': cobrancas.count(),
        'em_aberto': sum((m.valor_devido(hoje) for m in em_aberto), start=0),
        'atrasado': sum((m.valor_devido(hoje) for m in em_aberto if m.vencimento < hoje), start=0),
        'proxima': next((m for m in em_aberto if m.vencimento >= hoje), None),
    })


def _idade(nascimento, hoje):
    if nascimento is None:
        return None
    return hoje.year - nascimento.year - ((hoje.month, hoje.day) < (nascimento.month, nascimento.day))


@academia_required
@require_http_methods(['GET', 'POST'])
def aluno_form(request, pk=None, matricula_pk=None, nova_matricula=False):
    if nova_matricula and not request.administrador_academia:
        raise PermissionDenied("Somente o administrador autoriza uma nova matrícula para um aluno existente.")
    aluno = get_object_or_404(Atleta, pk=pk, academia=request.academia) if pk else None
    matricula = get_object_or_404(Matricula, pk=matricula_pk, academia=request.academia, atleta=aluno) if matricula_pk else None
    unidade_anterior = matricula.unidade_id if matricula else None
    # Situação antes da edição (os forms alteram as instâncias ao validar):
    # decide se a cobrança começa agora.
    matricula_ativa_antes = matricula.ativo if matricula else False
    aluno_ativo_antes = aluno.status == 'ativo' if aluno else False
    data = request.POST if request.method == 'POST' else None
    form = AlunoForm(data, instance=aluno, academia=request.academia)
    incluir_matricula = not pk or matricula_pk is not None or nova_matricula
    matricula_form = MatriculaForm(data, prefix='matricula', instance=matricula, academia=request.academia) if incluir_matricula else None
    if request.method == 'POST':
        valid = form.is_valid()
        if matricula_form is not None:
            valid = matricula_form.is_valid() and valid
        if valid:
            from financeiro.services import iniciar_cobranca, primeira_ativacao
            from .services_matricula import cobrar_taxa_se_primeira
            try:
                with transaction.atomic():
                    aluno = form.save()
                    comecam = {}  # matrícula -> 1º vencimento escolhido (None: o próximo)
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
                        if inscricao.ativo and not matricula_ativa_antes:
                            comecam[inscricao] = matricula_form.cleaned_data.get('primeiro_vencimento')
                    if pk and aluno.status == 'ativo' and not aluno_ativo_antes:
                        # Voltou de trancado/inativo: cobra a partir do próximo vencimento.
                        for ativa in aluno.matriculas.filter(ativo=True):
                            comecam.setdefault(ativa, None)
                    for inscricao_ativa, primeiro_vencimento in comecam.items():
                        primeira = primeira_ativacao(inscricao_ativa)
                        iniciar_cobranca(inscricao_ativa, primeiro_vencimento)
                        cobrar_taxa_se_primeira(inscricao_ativa, primeira)
            except ValidationError as error:
                form.add_error(None, ValidationError(error.messages))
            else:
                messages.success(request, 'Cadastro salvo com sucesso.')
                return redirect('portal:detalhe', pk=aluno.pk)
    return render(request, 'portal/form.html', {'form': form, 'matricula_form': matricula_form, 'aluno': aluno})


@academia_required
@require_http_methods(['GET', 'POST'])
def aluno_excluir(request, pk):
    """Apaga o aluno de vez — só sem pagamento registrado e sem Pix gerado,
    para não sumir com histórico financeiro. Com histórico, a tela oferece
    inativar (a cobrança para, os dados ficam). O responsável é apagado
    junto quando não tem outro aluno."""
    if not request.administrador_academia:
        raise PermissionDenied('Somente o administrador da academia exclui alunos.')
    from financeiro.models import CobrancaPix, Mensalidade

    aluno = get_object_or_404(Atleta, pk=pk, academia=request.academia)
    cobrancas = Mensalidade.objects.filter(matricula__atleta=aluno)
    pagas = cobrancas.filter(status='paga').count()
    com_pix = CobrancaPix.objects.filter(mensalidade__matricula__atleta=aluno).exists()
    responsavel = aluno.responsavel_financeiro
    if responsavel is not None and responsavel.academia_id != request.academia.pk:
        responsavel = None
    responsavel_sai = responsavel is not None and not responsavel.atletas.exclude(pk=aluno.pk).exists()

    if request.method == 'POST':
        acao = request.POST.get('acao')
        if acao == 'inativar':
            aluno.status = 'inativo'
            aluno.save(update_fields=['status'])
            messages.success(request, f'{aluno.nome}: aluno inativado. As mensalidades novas deixam de ser geradas.')
            return redirect('portal:detalhe', pk=aluno.pk)
        if acao == 'excluir' and not (pagas or com_pix):
            nome = aluno.nome
            with transaction.atomic():
                aluno.delete()
                if responsavel_sai:
                    responsavel.delete()
            messages.success(request, f'{nome}: cadastro excluído.')
            return redirect('portal:alunos')
        messages.error(request, 'Este aluno tem pagamento ou Pix registrado e não pode ser excluído. Inative o aluno.')
        return redirect('portal:excluir_aluno', pk=aluno.pk)

    partes = aluno.nome.split()
    return render(request, 'portal/aluno_excluir.html', {
        'aluno': aluno,
        'iniciais': ''.join(p[0] for p in (partes[:1] + partes[1:][-1:])).upper() or '?',
        'responsavel': responsavel,
        'responsavel_sai': responsavel_sai,
        'matriculas': aluno.matriculas.count(),
        'cobrancas': cobrancas.count(),
        'fichas': aluno.fichas_matricula.count(),
        'pagas': pagas,
        'com_pix': com_pix,
        'pode_excluir': not (pagas or com_pix),
    })


@academia_required
def financeiro_recibo(request, pk):
    from financeiro.models import Mensalidade
    from .views_responsavel import resposta_recibo

    mensalidade = get_object_or_404(
        Mensalidade.objects.select_related(
            'academia', 'matricula__atleta__responsavel_financeiro', 'matricula__turma', 'matricula__modalidade',
        ),
        pk=pk, academia=request.academia, status='paga',
    )
    return resposta_recibo(mensalidade)


def pagina_publica(request):
    from .models import ConviteMatricula, FotoPublica, PaginaPublica
    from .views_experimentais import contexto_publico
    return render(request, 'portal/publica.html', {
        **contexto_publico(request),
        'pagina': PaginaPublica.objects.order_by('pk').first(),
        'link_matricula': ConviteMatricula.link_do_site() is not None,
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
        campo = {'unidades': 'unidade', 'professores': 'turma__docente', 'turmas': 'turma', 'modalidades': 'modalidade'}[tipo]
        ativas = Matricula.objects.filter(academia=request.academia, ativo=True, **{campo: obj})
        partes = obj.nome.split()
        return render(request, 'portal/cadastro_detalhe.html', {
            'obj': obj, 'tipo': tipo, 'titulo': titulo, 'singular': singular,
            'iniciais': ''.join(p[0] for p in (partes[:1] + partes[1:][-1:])).upper() or '?',
            'alunos_ativos': ativas.values('atleta').distinct().count(),
            'matriculas_ativas': (
                # Um aluno com duas matrículas ativas na turma aparece uma vez.
                list({m.atleta_id: m for m in ativas.select_related('atleta').order_by('atleta__nome', '-pk')}.values())
                if tipo == 'turmas' else None
            ),
        })
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
    hoje = timezone.localdate()
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
    from financeiro.models import CobrancaPix
    cobrancas = (
        Mensalidade.objects.filter(academia=request.academia)
        .select_related('matricula__atleta')
        .prefetch_related(CobrancaPix.prefetch_ativas())
        .order_by('-vencimento', '-pk')
    )
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
    """Ativa a matrícula direto do cadastro do aluno, sem passar pelo
    formulário de edição: o administrador confere o dia de vencimento e
    define o 1º vencimento. Se ela veio de um convite de matrícula ainda
    'preenchido', ativa pelo mesmo caminho do convite (mantém o status do
    convite em sincronia)."""
    if not request.administrador_academia:
        raise PermissionDenied('Somente o administrador da academia ativa matrículas.')

    from .forms_matricula import AtivacaoMatriculaForm
    from .models import ConviteMatricula
    from .services_matricula import ativar_convite, ativar_matricula

    matricula = get_object_or_404(
        Matricula, pk=matricula_pk, atleta__pk=pk, academia=request.academia,
    )
    if matricula.ativo:
        messages.info(request, 'Esta matrícula já está ativa.')
        return redirect('portal:detalhe', pk=pk)

    form = AtivacaoMatriculaForm(request.POST, matricula=matricula, prefix=f'ativar-{matricula.pk}')
    if not form.is_valid():
        erros = ' '.join(erro for lista in form.errors.values() for erro in lista)
        messages.error(request, f'Matrícula não ativada: {erros}')
        return redirect('portal:detalhe', pk=pk)

    convite = ConviteMatricula.objects.filter(
        matricula=matricula, status=ConviteMatricula.PREENCHIDO,
    ).first()
    if convite is not None:
        ativar_convite(convite, **form.cleaned_data)
    else:
        ativar_matricula(matricula, **form.cleaned_data)
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
        return redirecionamento_seguro(request, 'portal:financeiro_cobrancas')
    messages.success(request, f'Mensalidade de {mensalidade.matricula.atleta.nome} marcada como paga.')

    # Pago por fora (dinheiro, transferência...): tira o Pix do ar para a
    # família não pagar de novo pelo código que recebeu no lembrete.
    from integracoes.woovi.exceptions import WooviError
    from integracoes.woovi.services import remover_cobranca_pix
    try:
        remover_cobranca_pix(mensalidade)
    except WooviError as error:
        logger.warning('Pix: falha ao cancelar o Pix da mensalidade %s: %s', mensalidade.pk, error)
        messages.warning(
            request,
            'Não foi possível cancelar o Pix desta mensalidade agora. Se a família pagar por ele, '
            'fale com o suporte para evitar pagamento em dobro.',
        )
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
    from integracoes.woovi.exceptions import WooviError

    mensalidade = get_object_or_404(
        Mensalidade.objects.select_related('matricula__atleta'), pk=pk, academia=request.academia
    )
    try:
        encerrar_mensalidade(mensalidade, request.POST.get('status', ''))
    except (ValueError, WooviError) as error:
        messages.error(
            request, f'Não foi possível alterar a mensalidade: {mensagem_erro_pix(error, "cancelar o Pix")}'
        )
    else:
        messages.success(
            request,
            f'{mensalidade.descricao} de {mensalidade.matricula.atleta.nome}: '
            f'{mensalidade.get_status_display().lower()}.',
        )
    return redirecionamento_seguro(request, 'portal:financeiro_cobrancas')


@academia_required
@require_http_methods(['POST'])
def financeiro_gerar_pix(request, pk):
    from financeiro.models import Mensalidade
    from integracoes.woovi.exceptions import WooviError
    from integracoes.woovi.services import garantir_cobranca_pix
    mensalidade = get_object_or_404(Mensalidade, pk=pk, academia=request.academia)
    try:
        garantir_cobranca_pix(mensalidade)
    except (ValueError, WooviError) as error:
        messages.error(request, mensagem_erro_pix(error, 'gerar o Pix'))
    else:
        messages.success(request, 'Pix gerado.')
    return redirecionamento_seguro(request, 'portal:financeiro_cobrancas')


@academia_required
def financeiro_pix_qrcode(request, pk):
    """Tela com o QR code e o Pix copia-e-cola já gerado. Abre como página
    normal (funciona sem JS) e também é usada como conteúdo do modal de
    detalhe (mesmo mecanismo de [data-detalhe]). Não chama o provedor: os
    dados do Pix ficam gravados na cobrança."""
    from financeiro.models import Mensalidade
    mensalidade = get_object_or_404(
        Mensalidade.objects.select_related('matricula__atleta', 'matricula__modalidade', 'matricula__unidade'),
        pk=pk, academia=request.academia,
    )
    cobranca = mensalidade.cobranca_pix_vigente
    erro = None
    if cobranca is None:
        erro = 'Esta mensalidade não tem um Pix vigente. Gere um novo pela lista de cobranças.'
    return render(request, 'portal/financeiro_pix.html', {'mensalidade': mensalidade, 'cobranca': cobranca, 'erro': erro})


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

    from integracoes.whatsapp.evolution import contexto_cobranca
    mensagem = f'Olá, {responsavel.nome}! {contexto_cobranca(mensalidade)} Pague por aqui: {link}'
    return render(request, 'portal/acesso_responsavel_gerado.html', {
        'aluno': aluno, 'responsavel': responsavel, 'link': link, 'mensagem': mensagem,
    })
