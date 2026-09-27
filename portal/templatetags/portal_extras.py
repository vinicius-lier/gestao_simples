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


@register.simple_tag
def pix_qrcode(br_code, escala=5):
    """QR Code do Pix gerado aqui mesmo, como SVG inline: nenhuma imagem é
    carregada de fora (a família não vê o endereço do provedor)."""
    if not br_code:
        return ''
    import segno
    from django.utils.safestring import mark_safe

    svg = segno.make(br_code, error='m').svg_inline(
        scale=escala, border=2, svgclass='pix-qrcode', title='QR Code para pagamento via Pix',
    )
    return mark_safe(svg)
