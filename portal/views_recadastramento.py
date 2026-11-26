import logging
import mimetypes
import os

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import content_disposition_header
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from atletas.arquivos import EXTENSOES_DOCUMENTO
from atletas.models import Atleta, DocumentoAtleta
from .forms_recadastramento import (
    CampanhaForm, DocumentoForm, FotoForm, RecadastramentoAnualForm, RecadastramentoPublicoForm,
)
from .models import CampanhaRecadastramento, PedidoRecadastramento
from .services_recadastramento import (
    abrir_campanha, alunos_do_publico, dados_iniciais, disparar_envio, encerrar_campanha,
    lembrar_quem_nao_respondeu, reenviar_pedido, responder_pedido,
)
from .views import academia_required

logger = logging.getLogger(__name__)


def _so_administrador(request):
    if not request.administrador_academia:
        raise PermissionDenied('Somente o administrador da academia abre e envia o recadastramento.')


def _avisar_envio(request, resultado):
    if resultado is True:
        messages.success(request, 'As mensagens estão saindo pelo WhatsApp, uma por família, com uma pausa entre elas. Acompanhe por aqui.')
    elif resultado['falhas'] and not resultado['enviados']:
        messages.warning(request, 'Não deu para enviar pelo WhatsApp. Confira a conexão em Configurações → WhatsApp e tente de novo, ou copie os links abaixo.')
    elif resultado['enviados'] or resultado['falhas']:
        texto = f'{resultado["enviados"]} mensagem(ns) enviada(s).'
        if resultado['falhas']:
            texto += f' {resultado["falhas"]} falharam e ficaram na fila.'
        messages.success(request, texto)


# ------------------------------------------------------------------ painel

@academia_required
@require_http_methods(['GET', 'POST'])
def recadastramentos(request):
    form = CampanhaForm(academia=request.academia)
    anual_form = RecadastramentoAnualForm(instance=request.academia, prefix='anual')
    if request.method == 'POST':
        _so_administrador(request)
        if request.POST.get('acao') == 'anual':
            anual_form = RecadastramentoAnualForm(request.POST, instance=request.academia, prefix='anual')
            if anual_form.is_valid():
                anual_form.save()
                messages.success(request, 'Recadastramento anual salvo.')
                return redirect('portal:recadastramentos')
        else:
            form = CampanhaForm(request.POST, academia=request.academia)
            if form.is_valid():
                d = form.cleaned_data
                alunos = alunos_do_publico(request.academia, d.get('turma'))
                if not alunos.exists():
                    form.add_error('turma', 'Não há alunos ativos para receber o recadastramento.')
                else:
                    campanha = abrir_campanha(
                        request.academia, titulo=d['titulo'], prazo=d['prazo'], alunos=alunos,
                        pedir_foto=d['pedir_foto'], documentos=d['documentos'], mensagem=d['mensagem'],
                        criada_por=request.user,
                    )
                    messages.success(request, f'Recadastramento aberto para {campanha.pedidos.count()} aluno(s).')
                    if d['enviar_agora']:
                        _avisar_envio(request, disparar_envio(campanha))
                    return redirect('portal:recadastramento_detalhe', pk=campanha.pk)
    campanhas = list(CampanhaRecadastramento.objects.filter(academia=request.academia))
    for campanha in campanhas:
        campanha.numeros = campanha.contagem()
    return render(request, 'portal/recadastramentos.html', {
        'form': form, 'anual_form': anual_form, 'campanhas': campanhas,
    })


@academia_required
def recadastramento_detalhe(request, pk):
    campanha = get_object_or_404(CampanhaRecadastramento, pk=pk, academia=request.academia)
    filtro = request.GET.get('status', '')
    pedidos = campanha.pedidos.select_related('atleta', 'atleta__responsavel_financeiro')
    if filtro in dict(PedidoRecadastramento.STATUS):
        pedidos = pedidos.filter(status=filtro)
    pedidos = list(pedidos)
    base = request.build_absolute_uri('/')[:-1]
    for pedido in pedidos:
        pedido.link = base + pedido.url()
    return render(request, 'portal/recadastramento_detalhe.html', {
        'campanha': campanha, 'numeros': campanha.contagem(), 'pedidos': pedidos, 'filtro': filtro,
        'status': PedidoRecadastramento.STATUS,
    })


@academia_required
@require_POST
def recadastramento_acao(request, pk):
    _so_administrador(request)
    campanha = get_object_or_404(CampanhaRecadastramento, pk=pk, academia=request.academia)
    acao = request.POST.get('acao')
    try:
        if acao == 'encerrar':
            encerrar_campanha(campanha)
            messages.success(request, 'Recadastramento encerrado. Os links não aceitam mais respostas.')
        elif acao == 'reabrir':
            campanha.encerrada_em = None
            campanha.save(update_fields=['encerrada_em'])
            messages.success(request, 'Recadastramento reaberto: os links voltam a aceitar respostas.')
        elif not campanha.aberta:
            raise ValidationError('Este recadastramento já foi encerrado.')
        elif acao == 'enviar':
            _avisar_envio(request, disparar_envio(campanha))
        elif acao == 'lembrar':
            n = lembrar_quem_nao_respondeu(campanha)
            if n:
                messages.info(request, f'Lembrete para {n} aluno(s) que ainda não responderam.')
                _avisar_envio(request, disparar_envio(campanha))
            else:
                messages.info(request, 'Ninguém aguardando resposta para lembrar.')
        elif acao == 'reenviar':
            pedido = get_object_or_404(campanha.pedidos, pk=request.POST.get('pedido'))
            reenviar_pedido(pedido)
            _avisar_envio(request, disparar_envio(campanha))
        else:
            raise ValidationError('Ação inválida.')
    except ValidationError as erro:
        messages.error(request, ' '.join(erro.messages))
    return redirect('portal:recadastramento_detalhe', pk=campanha.pk)


# ----------------------------------------------------------------- público

@require_http_methods(['GET', 'POST'])
def recadastramento_publico(request, token):
    pedido = PedidoRecadastramento.objects.select_related(
        'campanha', 'academia', 'atleta', 'atleta__responsavel_financeiro',
    ).filter(token=token).first()
    if pedido is None:
        return render(request, 'portal/recadastramento_publico.html', {'estado': 'invalido'}, status=404)
    if not pedido.aberto_para_resposta:
        estado = 'respondido' if pedido.status == PedidoRecadastramento.RESPONDIDO else 'encerrado'
        return render(request, 'portal/recadastramento_publico.html', {'estado': estado, 'pedido': pedido})

    if request.method == 'POST':
        form = RecadastramentoPublicoForm(request.POST, request.FILES, pedido=pedido)
    else:
        form = RecadastramentoPublicoForm(initial=dados_iniciais(pedido), pedido=pedido)
    if request.method == 'POST' and form.is_valid():
        try:
            responder_pedido(
                pedido, form.cleaned_data, foto=form.cleaned_data.get('foto'), documentos=form.documentos(),
            )
        except ValidationError as erro:
            form.add_error(None, erro)
        else:
            request.session['recadastramento_ok'] = pedido.atleta.nome
            return redirect('portal:recadastramento_recebido')
    return render(request, 'portal/recadastramento_publico.html', {'estado': 'ok', 'pedido': pedido, 'form': form})


def recadastramento_recebido(request):
    nome = request.session.pop('recadastramento_ok', None)
    if not nome:
        return redirect('portal:publica')
    return render(request, 'portal/recadastramento_publico.html', {'estado': 'recebido', 'nome': nome})


# ------------------------------------------------- foto e documentos (painel)

def _entregar(arquivo, nome, *, inline):
    """Entrega um arquivo privado: só pela view, sem cache compartilhado."""
    try:
        aberto = arquivo.open('rb')
    except (FileNotFoundError, OSError):
        raise Http404('Arquivo não encontrado.') from None
    ext = os.path.splitext(arquivo.name)[1].lower()
    tipo = EXTENSOES_DOCUMENTO.get(ext) or mimetypes.guess_type(arquivo.name)[0] or 'application/octet-stream'
    resposta = FileResponse(aberto, content_type=tipo)
    resposta['Content-Disposition'] = content_disposition_header(as_attachment=not inline, filename=nome)
    resposta['Cache-Control'] = 'private, no-store'
    resposta['X-Content-Type-Options'] = 'nosniff'
    return resposta


def _aluno(request, pk):
    return get_object_or_404(Atleta, pk=pk, academia=request.academia)


@academia_required
@require_GET
def aluno_foto(request, pk):
    aluno = _aluno(request, pk)
    if not aluno.foto:
        raise Http404('Aluno sem foto.')
    return _entregar(aluno.foto, f'foto-{aluno.pk}.jpg', inline=True)


@academia_required
@require_POST
def aluno_foto_enviar(request, pk):
    aluno = _aluno(request, pk)
    if request.POST.get('acao') == 'remover':
        aluno.remover_foto()
        messages.success(request, 'Foto removida.')
    else:
        form = FotoForm(request.POST, request.FILES)
        if form.is_valid():
            aluno.trocar_foto(form.cleaned_data['foto'])
            messages.success(request, 'Foto atualizada.')
        else:
            messages.error(request, ' '.join(e for erros in form.errors.values() for e in erros))
    return redirect(f'{aluno_url(aluno)}#documentos')


def aluno_url(aluno):
    from django.urls import reverse

    return reverse('portal:detalhe', args=[aluno.pk])


@academia_required
@require_POST
def aluno_documento_enviar(request, pk):
    aluno = _aluno(request, pk)
    form = DocumentoForm(request.POST, request.FILES)
    if form.is_valid():
        documento = form.save(commit=False)
        documento.academia = request.academia
        documento.atleta = aluno
        documento.enviado_por = request.user
        documento.nome_original = form.cleaned_data['arquivo'].name[:255]
        documento.tamanho = form.cleaned_data['arquivo'].size
        documento.save()
        messages.success(request, f'{documento.titulo} anexado.')
    else:
        messages.error(request, ' '.join(e for erros in form.errors.values() for e in erros))
    return redirect(f'{aluno_url(aluno)}#documentos')


@academia_required
@require_GET
def aluno_documento(request, pk, documento_pk):
    aluno = _aluno(request, pk)
    documento = get_object_or_404(DocumentoAtleta, pk=documento_pk, atleta=aluno, academia=request.academia)
    ext = os.path.splitext(documento.arquivo.name)[1].lower()
    nome = documento.nome_original or f'{documento.get_tipo_display()}{ext}'
    # PDF e imagens abrem no navegador; HEIC e o resto baixam.
    return _entregar(documento.arquivo, nome, inline=ext in ('.pdf', '.jpg', '.jpeg', '.png', '.webp'))


@academia_required
@require_POST
def aluno_documento_excluir(request, pk, documento_pk):
    aluno = _aluno(request, pk)
    documento = get_object_or_404(DocumentoAtleta, pk=documento_pk, atleta=aluno, academia=request.academia)
    titulo = documento.titulo
    documento.delete()
    messages.success(request, f'{titulo} excluído.')
    return redirect(f'{aluno_url(aluno)}#documentos')
