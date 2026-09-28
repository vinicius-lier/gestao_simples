import logging

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_POST

from .forms_matricula import (
    AtivacaoMatriculaForm, ConviteMatriculaForm, ImportacaoFichasForm, MatriculaPublicaForm,
)
from .models import ConviteMatricula, FichaMatricula
from .services_matricula import ativar_convite, cancelar_convite, efetivar_convite
from .views import academia_required

logger = logging.getLogger(__name__)


@academia_required
@require_http_methods(['GET', 'POST'])
def convites(request):
    lista = ConviteMatricula.objects.filter(academia=request.academia).select_related(
        'modalidade', 'turma', 'turma__unidade', 'atleta', 'matricula'
    )
    form = ConviteMatriculaForm(request.POST or None, academia=request.academia)
    if request.method == 'POST' and form.is_valid():
        convite = form.save(criado_por=request.user)
        messages.success(request, 'Link de matrícula gerado. Copie e envie para a família.')
        return redirect('portal:matricula_convite_detalhe', pk=convite.pk)
    return render(request, 'portal/matricula_convites.html', {'form': form, 'convites': lista})


@academia_required
@require_http_methods(['GET', 'POST'])
def importar_fichas(request):
    """Importa a planilha de respostas do formulário antigo. "Conferir" roda
    a importação inteira e desfaz no fim; "Importar" grava. Só administrador:
    cria alunos e matrículas ativas de uma vez."""
    from .importacao_fichas import PlanilhaInvalida, importar_planilha

    if not request.administrador_academia:
        raise PermissionDenied('Somente o administrador da academia importa alunos.')
    form = ImportacaoFichasForm(request.POST or None, request.FILES or None, academia=request.academia)
    relatorio = None
    if request.method == 'POST' and form.is_valid():
        gravar = request.POST.get('acao') == 'importar'
        try:
            relatorio = importar_planilha(
                request.academia, form.cleaned_data['arquivo'],
                turma=form.cleaned_data['turma'],
                cobrar_a_partir_de=form.cleaned_data['cobrar_a_partir_de'],
                gravar=gravar,
            )
        except PlanilhaInvalida as error:
            form.add_error('arquivo', str(error))
        else:
            if gravar:
                messages.success(
                    request,
                    f'Planilha importada: {relatorio.novos} aluno(s) novo(s) e '
                    f'{relatorio.existentes} ficha(s) anexada(s) a alunos que já existiam.',
                )
    return render(request, 'portal/matricula_importar.html', {'form': form, 'relatorio': relatorio})


@academia_required
def convite_detalhe(request, pk):
    convite = get_object_or_404(
        ConviteMatricula.objects.select_related(
            'modalidade', 'turma', 'turma__unidade', 'unidade',
            'atleta', 'atleta__responsavel_financeiro', 'matricula',
        ),
        pk=pk, academia=request.academia,
    )
    link = request.build_absolute_uri(convite.url())
    ativacao_form = None
    if convite.status == ConviteMatricula.PREENCHIDO and convite.matricula_id and request.administrador_academia:
        ativacao_form = AtivacaoMatriculaForm(matricula=convite.matricula)
    return render(request, 'portal/matricula_convite_detalhe.html', {
        'convite': convite, 'link': link, 'ativacao_form': ativacao_form,
        'ficha': FichaMatricula.objects.filter(convite=convite).first(),
    })


@academia_required
@require_POST
def convite_enviar(request, pk):
    from integracoes.whatsapp import enviar_convite_matricula
    from integracoes.whatsapp.base import WhatsAppProviderError

    convite = get_object_or_404(ConviteMatricula, pk=pk, academia=request.academia)
    if not convite.convidado_whatsapp:
        messages.error(request, 'Informe o WhatsApp da família antes de enviar.')
        return redirect('portal:matricula_convite_detalhe', pk=convite.pk)
    if not convite.aberto_para_preenchimento:
        messages.error(request, 'Este convite não está mais aberto para preenchimento.')
        return redirect('portal:matricula_convite_detalhe', pk=convite.pk)

    link = request.build_absolute_uri(convite.url())
    contexto = str(convite.turma or convite.modalidade)
    try:
        enviar_convite_matricula(
            request.academia, convite.convidado_nome, convite.convidado_whatsapp, link, contexto,
        )
    except (ValueError, WhatsAppProviderError) as error:
        messages.warning(request, f'Não deu para enviar automaticamente pelo WhatsApp: {error}')
    else:
        messages.success(request, 'Convite enviado por WhatsApp.')
    return redirect('portal:matricula_convite_detalhe', pk=convite.pk)


@academia_required
@require_POST
def convite_acao(request, pk):
    convite = get_object_or_404(
        ConviteMatricula.objects.select_related('matricula'), pk=pk, academia=request.academia,
    )
    acao = request.POST.get('acao')
    try:
        if acao == 'ativar':
            if not request.administrador_academia:
                raise PermissionDenied('Somente o administrador da academia ativa matrículas.')
            if convite.matricula_id is None:
                raise ValidationError('Só é possível ativar um convite já preenchido pela família.')
            form = AtivacaoMatriculaForm(request.POST, matricula=convite.matricula)
            if not form.is_valid():
                raise ValidationError([erro for erros in form.errors.values() for erro in erros])
            ativar_convite(convite, **form.cleaned_data)
            messages.success(request, 'Matrícula ativada.')
        elif acao == 'cancelar':
            cancelar_convite(convite)
            messages.success(request, 'Convite cancelado.')
        else:
            raise ValidationError('Ação inválida.')
    except ValidationError as error:
        messages.error(request, ' '.join(error.messages))
    return redirect('portal:matricula_convite_detalhe', pk=convite.pk)


@require_http_methods(['GET', 'POST'])
def matricula_convite(request, token):
    convite = ConviteMatricula.objects.select_related(
        'academia', 'modalidade', 'turma', 'turma__unidade', 'unidade'
    ).filter(token=token).first()
    if convite is None:
        return render(request, 'portal/matricula_convite.html', {'estado': 'invalido'}, status=404)
    if not convite.aberto_para_preenchimento:
        expirado = convite.status == ConviteMatricula.PENDENTE and convite.expirado
        return render(request, 'portal/matricula_convite.html',
                      {'estado': 'expirado' if expirado else 'usado', 'convite': convite})

    form = MatriculaPublicaForm(request.POST or None, academia=convite.academia)
    if request.method == 'POST' and form.is_valid():
        try:
            efetivar_convite(convite, form.cleaned_data)
        except ValidationError as error:
            form.add_error(None, error)
        else:
            _avisar_matricula_recebida(request, convite)
            request.session['matricula_convite_ok'] = str(convite.turma or convite.modalidade)
            return redirect('portal:matricula_convite_recebido')
    return render(request, 'portal/matricula_convite.html', {'estado': 'ok', 'convite': convite, 'form': form})


def _avisar_matricula_recebida(request, convite):
    """Avisa a escola pelo WhatsApp que há uma matrícula para conferir,
    definir o vencimento e ativar. Melhor esforço: a família nunca vê erro
    por causa do aviso — sem número cadastrado ou com o WhatsApp fora do ar,
    a matrícula segue em "Preenchido — revisar" na lista de convites."""
    from integracoes.whatsapp import enviar_aviso_escola
    from integracoes.whatsapp.base import WhatsAppProviderError

    link = request.build_absolute_uri(reverse('portal:matricula_convite_detalhe', args=[convite.pk]))
    texto = (
        f'Nova matrícula recebida: {convite.atleta.nome} — {convite.turma or convite.modalidade}. '
        f'Confira os dados, defina o vencimento e ative: {link}'
    )
    try:
        enviar_aviso_escola(convite.academia, texto)
    except (ValueError, WhatsAppProviderError) as erro:
        logger.warning('Aviso da matrícula do convite %s não enviado: %s', convite.pk, erro)


def matricula_convite_recebido(request):
    alvo = request.session.pop('matricula_convite_ok', None)
    if not alvo:
        return redirect('portal:publica')
    return render(request, 'portal/matricula_convite_recebido.html', {'alvo': alvo})
