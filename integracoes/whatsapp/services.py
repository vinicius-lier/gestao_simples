import os
import re

from integracoes.whatsapp.client import WhatsAppAPIError, WhatsAppClient


def normalizar_telefone(numero):
    """Só dígitos, com DDI 55 na frente quando parecer um número
    brasileiro sem DDI (10 ou 11 dígitos)."""
    digitos = re.sub(r"\D", "", numero or "")
    if digitos and not digitos.startswith("55") and len(digitos) in (10, 11):
        digitos = "55" + digitos
    return digitos


def enviar_acesso_portal_responsavel(responsavel, link):
    """Envia o link de acesso ao portal do responsável via WhatsApp.

    Requer um template aprovado no Meta Business Manager com duas
    variáveis de corpo, na ordem: nome do responsável e o link. Configure
    o nome do template em WHATSAPP_TEMPLATE_ACESSO (padrão: 'acesso_portal').
    """
    telefone = normalizar_telefone(responsavel.whatsapp)
    if not telefone:
        raise ValueError("O responsável não possui WhatsApp cadastrado.")

    template = os.getenv("WHATSAPP_TEMPLATE_ACESSO", "acesso_portal")
    cliente = WhatsAppClient()
    return cliente.enviar_template(
        telefone,
        nome_template=template,
        parametros=[responsavel.nome, link],
    )


def enviar_cobranca_responsavel(responsavel, mensalidade, link):
    """Avisa o responsável de uma mensalidade próxima do vencimento ou em
    aberto. Mesma exigência de template aprovado; configure o nome em
    WHATSAPP_TEMPLATE_COBRANCA (padrão: 'cobranca_mensalidade')."""
    telefone = normalizar_telefone(responsavel.whatsapp)
    if not telefone:
        raise ValueError("O responsável não possui WhatsApp cadastrado.")

    template = os.getenv("WHATSAPP_TEMPLATE_COBRANCA", "cobranca_mensalidade")
    cliente = WhatsAppClient()
    return cliente.enviar_template(
        telefone,
        nome_template=template,
        parametros=[
            responsavel.nome,
            f"{mensalidade.competencia:%m/%Y}",
            f"R$ {mensalidade.valor}",
            f"{mensalidade.vencimento:%d/%m/%Y}",
            link,
        ],
    )
