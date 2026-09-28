"""Recibo de pagamento em PDF de uma cobrança paga (mensalidade ou taxa de
matrícula) — baixado pela família no portal ou pela escola no painel.

Usa as fontes padrão do PDF (Helvetica), que só cobrem o latin-1: os textos
passam por ``_texto`` antes de entrar no documento."""
from decimal import ROUND_HALF_UP, Decimal

from django.contrib.staticfiles import finders
from django.utils import timezone
from fpdf import FPDF

from financeiro.models import CobrancaPix

# A mesma logo do painel e do site; sem o arquivo, o recibo sai sem logo.
LOGO = "portal/logo-fukuda.png"
TAMANHO_LOGO = 22  # mm

_MESES = (
    "janeiro", "fevereiro", "março", "abril", "maio", "junho",
    "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
)
_UNIDADES = (
    "zero", "um", "dois", "três", "quatro", "cinco", "seis", "sete", "oito", "nove",
    "dez", "onze", "doze", "treze", "quatorze", "quinze", "dezesseis", "dezessete",
    "dezoito", "dezenove",
)
_DEZENAS = ("", "", "vinte", "trinta", "quarenta", "cinquenta", "sessenta", "setenta", "oitenta", "noventa")
_CENTENAS = (
    "", "cento", "duzentos", "trezentos", "quatrocentos", "quinhentos",
    "seiscentos", "setecentos", "oitocentos", "novecentos",
)
_TROCAS = {"—": "-", "–": "-", "“": '"', "”": '"', "‘": "'", "’": "'", "…": "...", "•": "-", "·": "-"}


def _ate_mil(numero):
    if numero == 100:
        return "cem"
    centena, resto = divmod(numero, 100)
    partes = [_CENTENAS[centena]] if centena else []
    if resto:
        if resto < 20:
            partes.append(_UNIDADES[resto])
        else:
            dezena, unidade = divmod(resto, 10)
            partes.append(_DEZENAS[dezena] + (f" e {_UNIDADES[unidade]}" if unidade else ""))
    return " e ".join(partes)


def numero_por_extenso(numero):
    """Inteiro de 0 a 999.999 por extenso ("mil e duzentos", "cento e um")."""
    if numero == 0:
        return "zero"
    milhares, resto = divmod(numero, 1000)
    texto = ""
    if milhares:
        texto = "mil" if milhares == 1 else f"{_ate_mil(milhares)} mil"
    if resto:
        if not milhares:
            texto = _ate_mil(resto)
        elif resto < 100 or resto % 100 == 0:
            texto += f" e {_ate_mil(resto)}"
        else:
            texto += f" {_ate_mil(resto)}"
    return texto


def valor_por_extenso(valor):
    """R$ por extenso: "cento e cinquenta reais e vinte centavos"."""
    valor = Decimal(valor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    reais, centavos = int(valor), int((valor - int(valor)) * 100)
    if reais >= 1_000_000:
        return formatar_reais(valor)
    partes = []
    if reais:
        partes.append(f"{numero_por_extenso(reais)} {'real' if reais == 1 else 'reais'}")
    if centavos:
        partes.append(f"{numero_por_extenso(centavos)} {'centavo' if centavos == 1 else 'centavos'}")
    return " e ".join(partes) or "zero real"


def formatar_reais(valor):
    texto = f"{Decimal(valor):,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
    return f"R$ {texto}"


def data_por_extenso(data):
    return f"{data.day} de {_MESES[data.month - 1]} de {data.year}"


def _texto(valor):
    texto = str(valor)
    for original, troca in _TROCAS.items():
        texto = texto.replace(original, troca)
    return texto.encode("latin-1", "replace").decode("latin-1")


def _formatar_cpf(cpf):
    numeros = "".join(c for c in cpf or "" if c.isdigit())
    if len(numeros) != 11:
        return cpf or ""
    return f"{numeros[:3]}.{numeros[3:6]}.{numeros[6:9]}-{numeros[9:]}"


def nome_do_arquivo(mensalidade):
    aluno = "-".join(mensalidade.matricula.atleta.nome.lower().split())[:40]
    referencia = "taxa-matricula" if mensalidade.eh_taxa_matricula else f"{mensalidade.competencia:%Y-%m}"
    return f"recibo-{_texto(aluno).replace('?', '')}-{referencia}.pdf"


class _Recibo(FPDF):
    COR_TEXTO = (32, 32, 36)
    COR_SUAVE = (119, 119, 125)
    COR_DESTAQUE = (181, 36, 44)
    COR_LINHA = (232, 232, 234)


def gerar_recibo_pdf(mensalidade):
    """Bytes do PDF do recibo. Só para cobrança paga (ValueError senão)."""
    if mensalidade.status != "paga":
        raise ValueError("Só há recibo de cobrança paga.")

    matricula = mensalidade.matricula
    aluno = matricula.atleta
    academia = mensalidade.academia
    responsavel = aluno.responsavel_financeiro
    valor = mensalidade.valor_atual
    pago_em = timezone.localtime(mensalidade.pago_em) if mensalidade.pago_em else None
    pix = CobrancaPix.objects.filter(mensalidade=mensalidade, status=CobrancaPix.PAGA).order_by("-pago_em").first()

    pdf = _Recibo(format="A4", unit="mm")
    pdf.set_auto_page_break(False)
    pdf.set_margins(18, 18, 18)
    pdf.add_page()
    pdf.set_title(_texto(f"Recibo {mensalidade.pk:06d}"))
    pdf.set_author(_texto(academia.nome_fantasia or academia.nome))
    largura = pdf.w - 36

    # Cabeçalho: logo e escola à esquerda, número do recibo à direita.
    logo = finders.find(LOGO)
    x_texto = 18
    if logo:
        pdf.image(logo, x=18, y=16, w=TAMANHO_LOGO, h=TAMANHO_LOGO)
        x_texto += TAMANHO_LOGO + 5
    largura_texto = largura - (x_texto - 18)
    pdf.set_xy(x_texto, 20)
    pdf.set_text_color(*_Recibo.COR_TEXTO)
    pdf.set_font("Helvetica", "B", 15)
    pdf.cell(largura_texto * 0.62, 8, _texto(academia.nome_fantasia or academia.nome))
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(*_Recibo.COR_DESTAQUE)
    pdf.cell(largura_texto * 0.38, 8, _texto(f"RECIBO Nº {mensalidade.pk:06d}"), align="R")
    pdf.set_xy(x_texto, 28)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(*_Recibo.COR_SUAVE)
    contato = " - ".join(p for p in (
        academia.nome if academia.nome_fantasia else "",
        f"CNPJ {academia.cnpj}" if academia.cnpj else "",
        academia.telefone, academia.email,
    ) if p)
    pdf.multi_cell(largura_texto, 5, _texto(contato), new_x="LMARGIN", new_y="NEXT")
    # O texto do cabeçalho não pode terminar acima da logo.
    pdf.set_y(max(pdf.get_y(), 16 + (TAMANHO_LOGO if logo else 0)))
    pdf.ln(4)
    pdf.set_draw_color(*_Recibo.COR_LINHA)
    pdf.line(18, pdf.get_y(), 18 + largura, pdf.get_y())
    pdf.ln(10)

    # Título e valor em destaque.
    pdf.set_text_color(*_Recibo.COR_TEXTO)
    pdf.set_font("Helvetica", "B", 20)
    pdf.cell(largura * 0.6, 12, _texto("Recibo de pagamento"))
    pdf.set_fill_color(248, 238, 238)
    pdf.set_text_color(*_Recibo.COR_DESTAQUE)
    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(largura * 0.4, 12, _texto(formatar_reais(valor)), align="C", fill=True, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(10)

    # Declaração.
    pagador = responsavel.nome if responsavel else aluno.nome
    cpf = _formatar_cpf(responsavel.cpf if responsavel else aluno.cpf)
    turma = matricula.turma or matricula.modalidade
    declaracao = (
        f"Recebemos de {pagador}{f' (CPF {cpf})' if cpf else ''} a importância de "
        f"{formatar_reais(valor)} ({valor_por_extenso(valor)}), referente à "
        f"{mensalidade.descricao[:1].lower()}{mensalidade.descricao[1:]} "
        f"do(a) aluno(a) {aluno.nome}, {turma}."
    )
    pdf.set_text_color(*_Recibo.COR_TEXTO)
    pdf.set_font("Helvetica", "", 11)
    pdf.multi_cell(largura, 6.5, _texto(declaracao), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(8)

    # Detalhes.
    linhas = [
        ("Referência", mensalidade.descricao),
        ("Aluno(a)", aluno.nome),
        ("Turma", str(turma)),
        ("Vencimento", f"{mensalidade.vencimento:%d/%m/%Y}"),
        ("Data do pagamento", f"{pago_em:%d/%m/%Y às %H:%M}" if pago_em else "-"),
        ("Forma de pagamento", mensalidade.get_forma_pagamento_display() or "Não informada"),
        ("Valor pago", formatar_reais(valor)),
    ]
    if pix is not None:
        linhas.append(("Identificador do Pix", pix.transaction_id or pix.correlation_id))
    for rotulo, conteudo in linhas:
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(*_Recibo.COR_SUAVE)
        pdf.cell(55, 8, _texto(rotulo), border="B")
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_text_color(*_Recibo.COR_TEXTO)
        pdf.cell(largura - 55, 8, _texto(conteudo), border="B", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(14)

    if pago_em:
        pdf.set_font("Helvetica", "", 11)
        pdf.cell(largura, 6, _texto(data_por_extenso(pago_em.date())), new_x="LMARGIN", new_y="NEXT")

    # Rodapé.
    pdf.set_y(-28)
    pdf.set_draw_color(*_Recibo.COR_LINHA)
    pdf.line(18, pdf.get_y(), 18 + largura, pdf.get_y())
    pdf.ln(3)
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(*_Recibo.COR_SUAVE)
    agora = timezone.localtime()
    pdf.multi_cell(largura, 4.5, _texto(
        f"Recibo emitido eletronicamente por {academia.nome_fantasia or academia.nome} em "
        f"{agora:%d/%m/%Y às %H:%M}. Documento válido sem assinatura."
    ), align="C")
    return bytes(pdf.output())
