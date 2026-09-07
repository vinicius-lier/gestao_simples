import re
from datetime import date

from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from financeiro.models import LembreteCobranca, Mensalidade
from integracoes.whatsapp import enviar_cobranca as _enviar_cobranca_whatsapp
from integracoes.whatsapp.base import WhatsAppProviderError
from portal.models import TokenAcessoResponsavel

_URL_RE = re.compile(r"https?://\S+")


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


def _sanitizar_erro(exc, limite=300):
    """Mensagem de erro curta e sem segredos para gravar em
    LembreteCobranca.ultimo_erro — remove URLs (que carregam token de
    acesso) e trunca."""
    texto = _URL_RE.sub("[url]", str(exc))
    return texto[:limite]


def preparar_payload_n8n(mensalidade, tipo_lembrete, responsavel=None):
    """Sequência FASE 2 (preparada, ainda não acionada por enviar_lembretes):

        garante cobrança Asaas UNDEFINED
        -> obtém Pix copia-e-cola
        -> gera link público de pagamento
        -> monta o payload para o n8n

    Não envia nada. Devolve o dict de payload (ver
    integracoes.n8n.services.montar_payload_cobranca)."""
    from integracoes.asaas.client import AsaasAPIError
    from integracoes.asaas.services import garantir_cobranca_asaas, obter_pix_mensalidade
    from integracoes.n8n.services import montar_payload_cobranca

    if responsavel is None:
        responsavel = mensalidade.matricula.atleta.responsavel_financeiro

    pix_copia_e_cola = ""
    try:
        garantir_cobranca_asaas(mensalidade)
        pix = obter_pix_mensalidade(mensalidade)
        pix_copia_e_cola = (pix or {}).get("payload") or ""
    except (ValueError, AsaasAPIError):
        # segue sem Pix — a mensagem ainda leva o link público
        pass

    link = montar_link_pagamento(mensalidade, responsavel)
    return montar_payload_cobranca(
        mensalidade,
        tipo_lembrete,
        link_pagamento=link,
        pix_copia_e_cola=pix_copia_e_cola,
        asaas_invoice_url=mensalidade.asaas_invoice_url or "",
    )


def enviar_lembretes(hoje=None, academia=None):
    """Manda, uma única vez por mensalidade e por estágio, o lembrete de
    cobrança pelo WhatsApp: 5 dias antes do vencimento, 1 dia antes, no
    dia e quando a mensalidade fica atrasada.

    Cada estágio vira um LembreteCobranca criado ANTES da tentativa:

      * sucesso  -> status=enviado (não é reenviado);
      * falha    -> status=erro (retentado na próxima execução);
      * sem responsável / sem WhatsApp -> nada é registrado, tenta depois.

    Mensalidades pagas/canceladas nem entram na fila (em_aberto()).

    Retorna a lista de pks das mensalidades para as quais um lembrete foi
    efetivamente enviado nesta chamada.
    """
    hoje = hoje or date.today()

    qs = Mensalidade.objects.em_aberto().select_related(
        "academia",
        "matricula__modalidade",
        "matricula__atleta__responsavel_financeiro",
    )
    if academia is not None:
        qs = qs.filter(academia=academia)

    gerar_cobranca = getattr(settings, "LEMBRETES_GERAM_COBRANCA_ASAAS", False)
    enviados = []

    for mensalidade in qs:
        estagio = calcular_estagio(mensalidade, hoje)
        if not estagio:
            continue

        lembrete = LembreteCobranca.objects.filter(
            mensalidade=mensalidade, estagio=estagio
        ).first()
        if lembrete is not None and lembrete.status == LembreteCobranca.ENVIADO:
            continue

        aluno = mensalidade.matricula.atleta
        responsavel = aluno.responsavel_financeiro
        if responsavel is None or not responsavel.whatsapp:
            continue

        if lembrete is None:
            lembrete = LembreteCobranca.objects.create(
                mensalidade=mensalidade, estagio=estagio
            )

        # FASE 2 (desligada por padrão): garantir a cobrança Asaas UNDEFINED
        # antes do envio, para o Pix copia-e-cola já existir na mensagem.
        if gerar_cobranca:
            _garantir_cobranca_silenciosa(mensalidade)

        link = montar_link_pagamento(mensalidade, responsavel)

        lembrete.tentativas += 1
        try:
            resultado = _enviar_cobranca_whatsapp(
                mensalidade.academia, responsavel, mensalidade, link
            )
        except (ValueError, WhatsAppProviderError) as exc:
            lembrete.status = LembreteCobranca.ERRO
            lembrete.ultimo_erro = _sanitizar_erro(exc)
            lembrete.save(
                update_fields=["status", "tentativas", "ultimo_erro", "atualizado_em"]
            )
            continue

        lembrete.status = LembreteCobranca.ENVIADO
        lembrete.enviado_em = timezone.now()
        lembrete.provider = (resultado or {}).get("provider", "")
        lembrete.provider_message_id = (resultado or {}).get("message_id", "")
        lembrete.ultimo_erro = ""
        lembrete.save(
            update_fields=[
                "status",
                "tentativas",
                "enviado_em",
                "provider",
                "provider_message_id",
                "ultimo_erro",
                "atualizado_em",
            ]
        )
        enviados.append(mensalidade.pk)

    return enviados


def _garantir_cobranca_silenciosa(mensalidade):
    from integracoes.asaas.client import AsaasAPIError
    from integracoes.asaas.services import garantir_cobranca_asaas

    try:
        garantir_cobranca_asaas(mensalidade)
    except (ValueError, AsaasAPIError):
        # best effort: sem cobrança, o lembrete ainda vai com o link público
        pass
