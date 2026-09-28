"""Portal do responsável financeiro: área pública (sem o login de staff)
onde ele acompanha as mensalidades dos alunos, paga por Pix e baixa os
recibos. Entra com CPF ou WhatsApp + senha; a senha é criada pela família no
primeiro acesso pelo link de uso único que chega no WhatsApp (ver
TokenAcessoResponsavel), e o mesmo link serve para o "esqueci a senha".
"""
import logging
import re
from datetime import timedelta
from functools import wraps
from urllib.parse import urlencode

from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from atletas.models import Atleta, Responsavel
from financeiro.models import Mensalidade
from .forms_responsavel import (
    EsqueciSenhaForm, LoginResponsavelForm, SenhaResponsavelForm, responsaveis_pelo_login,
)
from .models import TokenAcessoResponsavel

logger = logging.getLogger(__name__)

_PADRAO_DESTINO_PAGAR = re.compile(r'^/responsavel/mensalidade/(\d+)/pagar/$')
_PADRAO_DESTINO_PORTAL = re.compile(r'^/responsavel/[\w/.-]*$')

# "Esqueci a senha" não manda outro link para o mesmo responsável antes disso.
INTERVALO_ENTRE_LINKS = timedelta(minutes=2)
VALIDADE_LINK_SENHA_HORAS = 1


def destino_pos_login(proximo, responsavel):
    """Só aceita redirecionar, após o login por link, para a página de
    pagamento de uma mensalidade que realmente pertence a esse
    responsável, ou para a tela de senha — nunca uma URL externa nem de
    outra família."""
    if proximo == reverse('portal:responsavel_senha'):
        return proximo
    correspondencia = _PADRAO_DESTINO_PAGAR.match(proximo or '')
    if not correspondencia:
        return None
    existe = Mensalidade.objects.filter(
        pk=correspondencia.group(1), matricula__atleta__responsavel_financeiro=responsavel
    ).exists()
    return proximo if existe else None


def _entrar(request, responsavel, *, via_link):
    request.session.cycle_key()
    request.session['responsavel_id'] = responsavel.pk
    # Entrou pelo link do WhatsApp: pode criar/trocar a senha sem a atual.
    request.session['responsavel_via_link'] = via_link
    request.session.set_expiry(60 * 60 * 24 * 14)  # 14 dias, como o padrão de sessão do Django


def responsavel_required(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        responsavel = Responsavel.objects.filter(
            pk=request.session.get('responsavel_id'), academia__ativo=True
        ).select_related('academia').first()
        if not responsavel:
            request.session.pop('responsavel_id', None)
            destino = reverse('portal:responsavel_login')
            if request.method == 'GET':
                destino += '?' + urlencode({'next': request.path})
            return redirect(destino)
        request.responsavel = responsavel
        return view(request, *args, **kwargs)
    return wrapped


@require_http_methods(['GET', 'POST'])
def responsavel_login(request):
    """Entrada com CPF ou WhatsApp + senha."""
    proximo = request.GET.get('next', '')
    if not _PADRAO_DESTINO_PORTAL.match(proximo) or '//' in proximo:
        proximo = ''
    if request.session.get('responsavel_id') and Responsavel.objects.filter(
        pk=request.session['responsavel_id'], academia__ativo=True,
    ).exists():
        return redirect(proximo or 'portal:responsavel_painel')

    form = LoginResponsavelForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        _entrar(request, form.responsavel, via_link=False)
        return redirect(proximo or 'portal:responsavel_painel')
    return render(request, 'portal/responsavel_login.html', {'form': form})


@require_http_methods(['GET', 'POST'])
def responsavel_esqueci(request):
    """Primeiro acesso ou senha esquecida: manda um link de acesso para o
    WhatsApp cadastrado, que leva à tela de criar a senha. A resposta é a
    mesma exista ou não o cadastro (não revela quem é cliente)."""
    from integracoes.whatsapp import enviar_acesso
    from integracoes.whatsapp.base import WhatsAppProviderError

    form = EsqueciSenhaForm(request.POST or None)
    enviado = False
    if request.method == 'POST' and form.is_valid():
        enviado = True
        limite = timezone.now() - INTERVALO_ENTRE_LINKS
        for responsavel in responsaveis_pelo_login(form.cleaned_data['identificacao'])[:3]:
            if not responsavel.whatsapp:
                continue
            if responsavel.tokens_acesso.filter(criado_em__gt=limite).exists():
                continue
            acesso = TokenAcessoResponsavel.gerar(responsavel, validade_horas=VALIDADE_LINK_SENHA_HORAS)
            link = request.build_absolute_uri(
                reverse('portal:responsavel_entrar', args=[acesso.token])
            ) + '?' + urlencode({'next': reverse('portal:responsavel_senha')})
            try:
                enviar_acesso(responsavel.academia, responsavel, link)
            except (ValueError, WhatsAppProviderError) as erro:
                logger.warning('Link de senha do responsável %s não enviado: %s', responsavel.pk, erro)
    return render(request, 'portal/responsavel_esqueci.html', {'form': form, 'enviado': enviado})


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
    _entrar(request, acesso.responsavel, via_link=True)
    messages.success(request, f'Bem-vindo(a), {acesso.responsavel.nome}.')
    if not acesso.responsavel.tem_senha and not request.GET.get('next'):
        # Primeiro acesso: já oferece criar a senha para as próximas vezes.
        return redirect('portal:responsavel_senha')
    return seguir()


def responsavel_link_expirado(request):
    return render(request, 'portal/responsavel_link_expirado.html')


@responsavel_required
@require_http_methods(['POST'])
def responsavel_sair(request):
    request.session.pop('responsavel_id', None)
    request.session.pop('responsavel_via_link', None)
    return redirect('portal:responsavel_login')


@responsavel_required
@require_http_methods(['GET', 'POST'])
def responsavel_senha(request):
    responsavel = request.responsavel
    pedir_atual = responsavel.tem_senha and not request.session.get('responsavel_via_link')
    form = SenhaResponsavelForm(request.POST or None, responsavel=responsavel, pedir_senha_atual=pedir_atual)
    if request.method == 'POST' and form.is_valid():
        criada = not responsavel.tem_senha
        responsavel.definir_senha(form.cleaned_data['nova_senha'])
        messages.success(
            request,
            'Senha criada. Da próxima vez, entre com seu CPF ou WhatsApp e esta senha.'
            if criada else 'Senha alterada.',
        )
        return redirect('portal:responsavel_painel')
    return render(request, 'portal/responsavel_senha.html', {'form': form, 'criando': not responsavel.tem_senha})


def _cobrancas_do_responsavel(responsavel):
    return Mensalidade.objects.filter(
        academia_id=responsavel.academia_id,
        matricula__atleta__responsavel_financeiro=responsavel,
    )


@responsavel_required
def responsavel_painel(request):
    responsavel = request.responsavel
    Mensalidade.objects.filter(academia_id=responsavel.academia_id).marcar_vencidas()
    hoje = timezone.localdate()
    alunos = Atleta.objects.filter(
        responsavel_financeiro=responsavel, academia_id=responsavel.academia_id
    ).order_by('nome')
    linhas = [
        {
            'aluno': aluno,
            'turmas': [m.turma or m.modalidade for m in aluno.matriculas.filter(ativo=True)],
            'mensalidades': _cobrancas_do_responsavel(responsavel).filter(
                matricula__atleta=aluno,
            ).select_related('matricula__modalidade').order_by('-vencimento', '-pk')[:12],
        }
        for aluno in alunos
    ]
    em_aberto = list(_cobrancas_do_responsavel(responsavel).em_aberto().order_by('vencimento'))
    partes = responsavel.nome.split()
    return render(request, 'portal/responsavel_painel.html', {
        'linhas': linhas,
        'iniciais': ''.join(p[0] for p in (partes[:1] + partes[1:][-1:])).upper() or '?',
        'em_aberto': sum((m.valor_devido(hoje) for m in em_aberto), start=0),
        'atrasado': sum((m.valor_devido(hoje) for m in em_aberto if m.vencimento < hoje), start=0),
        'proxima': next((m for m in em_aberto if m.vencimento >= hoje), None),
    })


@responsavel_required
def responsavel_pagar(request, pk):
    from integracoes.woovi.exceptions import WooviError
    from integracoes.woovi.services import garantir_cobranca_pix

    mensalidade = get_object_or_404(
        Mensalidade.objects.select_related('matricula__atleta', 'matricula__modalidade', 'matricula__unidade'),
        pk=pk,
        academia_id=request.responsavel.academia_id,
        matricula__atleta__responsavel_financeiro=request.responsavel,
    )

    cobranca, erro = None, None
    if mensalidade.status in ('pendente', 'vencida'):
        try:
            cobranca = garantir_cobranca_pix(mensalidade)
        except (ValueError, WooviError) as error:
            # A família não vê detalhe técnico nem o nome do provedor.
            logger.warning('Pix: falha ao gerar o Pix da mensalidade %s: %s', mensalidade.pk, error)
            erro = (
                'Não foi possível carregar o Pix agora. Tente novamente em instantes '
                'ou fale com a secretaria.'
            )

    return render(request, 'portal/responsavel_pagar.html', {
        'mensalidade': mensalidade, 'cobranca': cobranca, 'erro': erro,
    })


@responsavel_required
def responsavel_recibo(request, pk):
    mensalidade = get_object_or_404(
        _cobrancas_do_responsavel(request.responsavel).select_related(
            'academia', 'matricula__atleta__responsavel_financeiro', 'matricula__turma', 'matricula__modalidade',
        ),
        pk=pk, status='paga',
    )
    return resposta_recibo(mensalidade)


def resposta_recibo(mensalidade):
    """O PDF do recibo como download."""
    from financeiro.recibo import gerar_recibo_pdf, nome_do_arquivo

    resposta = HttpResponse(gerar_recibo_pdf(mensalidade), content_type='application/pdf')
    resposta['Content-Disposition'] = f'attachment; filename="{nome_do_arquivo(mensalidade)}"'
    return resposta
