from datetime import date

from django.conf import settings
from django.urls import reverse

from financeiro.models import LembreteCobranca, Mensalidade
from integracoes.whatsapp.client import WhatsAppAPIError
from integracoes.whatsapp.services import enviar_cobranca_responsavel
from portal.models import TokenAcessoResponsavel


def calcular_estagio(mensalidade, hoje=None):
    """Em qual estágio do lembrete a mensalidade está hoje, ou None se
    nenhum se aplica (faltam mais de 5 dias, ou está entre 2 e 4 dias)."""
    hoje = hoje or date.today()
    dias = (mensalidade.vencimento - hoje).days

    if dias == 5:
        return LembreteCobranca.CINCO_DIAS
    if dias == 1:
        return LembreteCobranca.UM_DIA
    if dias == 0:
        return LembreteCobranca.VENCIMENTO
    if dias < 0:
        return LembreteCobranca.ATRASADA
    return None


def montar_link_pagamento(mensalidade, responsavel):
    """Gera um link de acesso de uso único que leva direto para a tela de
    pagamento da mensalidade — usado fora de uma request (comando), por
    isso monta a URL absoluta a partir de SITE_URL em vez de
    request.build_absolute_uri."""
    acesso = TokenAcessoResponsavel.gerar(responsavel)
    entrada = reverse("portal:responsavel_entrar", args=[acesso.token])
    destino = reverse("portal:responsavel_pagar", args=[mensalidade.pk])
    base = settings.SITE_URL.rstrip("/")
    return f"{base}{entrada}?next={destino}"


def enviar_lembretes(hoje=None, academia=None):
    """Manda, uma única vez por mensalidade e por estágio, o lembrete de
    cobrança pelo WhatsApp: 5 dias antes do vencimento, 1 dia antes, no
    dia e quando a mensalidade fica atrasada.

    Mensalidades sem responsável financeiro com WhatsApp cadastrado, ou
    cujo envio falhe (WhatsApp não configurado, API fora do ar etc.), não
    ficam marcadas como enviadas — tentamos de novo na próxima chamada.

    Retorna a lista de pks das mensalidades para as quais um lembrete foi
    efetivamente enviado nesta chamada.
    """
    hoje = hoje or date.today()

    qs = Mensalidade.objects.em_aberto().select_related(
        "matricula__atleta__responsavel_financeiro"
    )
    if academia is not None:
        qs = qs.filter(academia=academia)

    enviados = []
    for mensalidade in qs:
        estagio = calcular_estagio(mensalidade, hoje)
        if not estagio:
            continue

        if LembreteCobranca.objects.filter(mensalidade=mensalidade, estagio=estagio).exists():
            continue

        aluno = mensalidade.matricula.atleta
        responsavel = aluno.responsavel_financeiro
        if responsavel is None or not responsavel.whatsapp:
            continue

        link = montar_link_pagamento(mensalidade, responsavel)
        try:
            enviar_cobranca_responsavel(responsavel, mensalidade, link)
        except (ValueError, WhatsAppAPIError):
            continue

        LembreteCobranca.objects.create(mensalidade=mensalidade, estagio=estagio)
        enviados.append(mensalidade.pk)

    return enviados
