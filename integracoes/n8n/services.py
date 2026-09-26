"""Montagem do payload Django -> n8n.

`montar_payload_cobranca` é pura: não faz I/O, não gera token, não chama a
Woovi. Os valores derivados de integrações externas (link público já
assinado, Pix copia-e-cola) entram por parâmetro, calculados por quem tem
o contexto (ver financeiro.lembretes). A família só recebe links do próprio
sistema — nunca a página hospedada do provedor de pagamento.
"""
from datetime import date

EVENTO_COBRANCA = "cobranca_lembrete"

# Campos que NUNCA podem ir no payload (checado nos testes):
CAMPOS_PROIBIDOS = frozenset(
    {
        "cpf",
        "email",
        "endereco",
        "correlation_id",
        "link_pagamento_pix",
        "token",
        "api_key",
        "credential",
        "credencial",
        "pix_qrcode_base64",
    }
)


def _competencia(mensalidade):
    return mensalidade.competencia.strftime("%Y-%m")


def _dias_para_vencer(mensalidade, hoje):
    return (mensalidade.vencimento - hoje).days


def montar_payload_cobranca(
    mensalidade,
    tipo_lembrete,
    *,
    link_pagamento="",
    pix_copia_e_cola="",
    hoje=None,
):
    """Monta o dict que o Django enviará ao n8n para um lembrete de cobrança.

    `tipo_lembrete` é um dos valores de ``LembreteCobranca.ESTAGIOS``
    (``5_dias``, ``1_dia``, ``vencimento``, ``atrasada``).
    """
    hoje = hoje or date.today()

    matricula = mensalidade.matricula
    aluno = matricula.atleta
    academia = mensalidade.academia
    responsavel = aluno.responsavel_financeiro

    if responsavel is None:
        raise ValueError(
            "A mensalidade não tem responsável financeiro para notificar."
        )

    # Imports tardios: evitam acoplar este pacote no load.
    from academias.models import IntegracaoWhatsApp
    from integracoes.whatsapp import normalizar_telefone

    config = IntegracaoWhatsApp.objects.filter(academia=academia).first()
    instancia_whatsapp = config.evolution_instance_name if config is not None else ""
    instancia_whatsapp = instancia_whatsapp or ""


    return {
        "evento": EVENTO_COBRANCA,
        "idempotency_key": f"{mensalidade.pk}:{tipo_lembrete}",
        "academia_id": academia.pk,
        "academia_nome": academia.nome,
        "instancia_whatsapp": instancia_whatsapp,
        "mensalidade_id": mensalidade.pk,
        "tipo_lembrete": tipo_lembrete,
        "status_mensalidade": mensalidade.status,
        "competencia": _competencia(mensalidade),
        "valor": str(mensalidade.valor),
        "vencimento": mensalidade.vencimento.isoformat(),
        "dias_para_vencer": _dias_para_vencer(mensalidade, hoje),
        "aluno_nome": aluno.nome,
        "modalidade": matricula.modalidade.nome,
        "responsavel_id": responsavel.pk,
        "responsavel_nome": responsavel.nome,
        "telefone": normalizar_telefone(responsavel.whatsapp),
        "link_pagamento": link_pagamento or "",
        "pix_copia_e_cola": pix_copia_e_cola or "",
    }
