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
def link_whatsapp(numero, mensagem):
    """wa.me genérico: qualquer número + mensagem prontos."""
    numero = _numero_whatsapp(numero)
    if not numero:
        return ''
    return f"https://wa.me/{numero}?text={quote(mensagem)}"
