from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from .forms_matricula import ConviteMatriculaForm, MatriculaPublicaForm
from .models import ConviteMatricula
from .services_matricula import ativar_convite, cancelar_convite, efetivar_convite
from .views import academia_required


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
def convite_detalhe(request, pk):
    convite = get_object_or_404(
        ConviteMatricula.objects.select_related(
            'modalidade', 'turma', 'turma__unidade', 'unidade',
            'atleta', 'atleta__responsavel_financeiro', 'matricula',
        ),
        pk=pk, academia=request.academia,
    )
    link = request.build_absolute_uri(convite.url())
    return render(request, 'portal/matricula_convite_detalhe.html', {'convite': convite, 'link': link})


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
    convite = get_object_or_404(ConviteMatricula, pk=pk, academia=request.academia)
    acao = request.POST.get('acao')
    try:
        if acao == 'ativar':
            ativar_convite(convite)
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

    form = MatriculaPublicaForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        try:
            efetivar_convite(convite, form.cleaned_data)
        except ValidationError as error:
            form.add_error(None, error)
        else:
            request.session['matricula_convite_ok'] = str(convite.turma or convite.modalidade)
            return redirect('portal:matricula_convite_recebido')
    return render(request, 'portal/matricula_convite.html', {'estado': 'ok', 'convite': convite, 'form': form})


def matricula_convite_recebido(request):
    alvo = request.session.pop('matricula_convite_ok', None)
    if not alvo:
        return redirect('portal:publica')
    return render(request, 'portal/matricula_convite_recebido.html', {'alvo': alvo})
