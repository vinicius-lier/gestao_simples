import re
from urllib.parse import quote

from django import template

register = template.Library()


def _numero_whatsapp(digitos):
    digitos = re.sub(r'\D', '', digitos or '')
    if digitos and not digitos.startswith('55') and len(digitos) in (10, 11):
        digitos = '55' + digitos
    return digitos


@register.simple_tag
def link_cobranca_whatsapp(mensalidade):
    """Monta o link wa.me para cobrar uma mensalidade, já com a mensagem
    preenchida. Retorna vazio se não houver responsável ou WhatsApp
    cadastrado."""
    aluno = mensalidade.matricula.atleta
    responsavel = aluno.responsavel_financeiro
    numero = _numero_whatsapp(getattr(responsavel, 'whatsapp', ''))
    if not numero:
        return ''

    mensagem = (
        f"Olá! A mensalidade de {aluno.nome} referente a "
        f"{mensalidade.competencia:%m/%Y} está no valor de R$ {mensalidade.valor}, "
        f"com vencimento em {mensalidade.vencimento:%d/%m/%Y}."
    )
    return f"https://wa.me/{numero}?text={quote(mensagem)}"
