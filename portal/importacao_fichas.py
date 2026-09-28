"""Importação da planilha de respostas do formulário de matrícula antigo
(Google Forms exportado em .xlsx) para dentro do sistema.

Cada linha da planilha vira uma ``FichaMatricula``. Aluno que ainda não
existe (comparado pelo nome, sem acento/maiúscula) é criado com responsável
e matrícula ativa na turma escolhida — a cobrança começa no próximo
vencimento a partir da data escolhida, sem taxa de matrícula. Aluno que já
existe só ganha a ficha: o cadastro dele não muda.

``importar_planilha`` sempre roda numa transação; sem ``gravar`` ela é
desfeita no fim, então a conferência mostra exatamente o que a importação
vai fazer. O arquivo é lido só com a biblioteca padrão (um .xlsx é um zip de
XMLs) e com limites de tamanho, porque vem de upload.
"""
import re
import unicodedata
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from atletas.models import Atleta, Responsavel
from matriculas.models import Matricula
from .forms import digits
from .models_matricula import PERGUNTAS_SAUDE, FichaMatricula

TAMANHO_MAXIMO_XML = 20 * 1024 * 1024
LINHAS_MAXIMAS = 2000
_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_EPOCA_EXCEL = datetime(1899, 12, 30)
_TELEFONE_RE = re.compile(r"(?:\+?55[\s.-]*)?\(?\d{2}\)?[\s.-]*9?[\s.-]*\d{4}[\s.-]*\d{4}")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_NUMERO_CIENTIFICO_RE = re.compile(r"\d+(?:\.\d+)?E\+?\d+", re.IGNORECASE)


class PlanilhaInvalida(ValueError):
    """O arquivo não é a planilha de respostas esperada."""


# ------------------------------------------------------------------ leitura
def _indice_coluna(referencia):
    letras = re.match(r"[A-Z]+", referencia).group()
    indice = 0
    for letra in letras:
        indice = indice * 26 + (ord(letra) - 64)
    return indice - 1


def _xml(pacote, nome):
    info = pacote.getinfo(nome)
    if info.file_size > TAMANHO_MAXIMO_XML:
        raise PlanilhaInvalida("A planilha é grande demais para importar.")
    return ET.fromstring(pacote.read(nome))


def ler_xlsx(arquivo):
    """Linhas (listas de texto) da primeira aba do .xlsx; a primeira é o
    cabeçalho. Números vêm como texto ("44936.577")."""
    try:
        pacote = zipfile.ZipFile(arquivo)
    except zipfile.BadZipFile:
        raise PlanilhaInvalida("O arquivo não é uma planilha .xlsx.") from None
    with pacote:
        nomes = pacote.namelist()
        abas = sorted(n for n in nomes if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n))
        if not abas:
            raise PlanilhaInvalida("O arquivo não é uma planilha .xlsx.")
        textos = []
        if "xl/sharedStrings.xml" in nomes:
            for item in _xml(pacote, "xl/sharedStrings.xml").iter(f"{_NS}si"):
                textos.append("".join(t.text or "" for t in item.iter(f"{_NS}t")))
        # A primeira aba (sheet1) — é onde o Google Forms grava as respostas.
        aba = min(abas, key=lambda n: int(re.search(r"(\d+)\.xml$", n).group(1)))
        linhas = []
        for linha in _xml(pacote, aba).iter(f"{_NS}row"):
            valores = {}
            for celula in linha.iter(f"{_NS}c"):
                tipo, valor = celula.get("t"), celula.find(f"{_NS}v")
                if tipo == "s" and valor is not None:
                    texto = textos[int(valor.text)]
                elif tipo == "inlineStr":
                    texto = "".join(t.text or "" for t in celula.iter(f"{_NS}t"))
                else:
                    texto = valor.text if valor is not None and valor.text else ""
                valores[_indice_coluna(celula.get("r"))] = texto
            if valores:
                linhas.append([valores.get(i, "") for i in range(max(valores) + 1)])
            if len(linhas) > LINHAS_MAXIMAS + 1:
                raise PlanilhaInvalida(f"A planilha passa de {LINHAS_MAXIMAS} respostas.")
    return linhas


# ------------------------------------------------------------ interpretação
def normalizar(texto):
    """Sem acento, minúsculo e com espaços simples — para comparar nomes e
    cabeçalhos."""
    sem_acento = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode()
    return " ".join(sem_acento.casefold().split())


def _mapear_colunas(cabecalho):
    """Índice de cada campo, pelo texto da pergunta no cabeçalho. Os termos e
    a declaração guardam também o próprio texto (é o que a família aceitou)."""
    colunas, textos = {}, {}
    perguntas = {normalizar(p): c for c, p, _cd, _pd in PERGUNTAS_SAUDE}
    perguntas.update({normalizar(pd): cd for _c, _p, cd, pd in PERGUNTAS_SAUDE if cd})
    for indice, bruto in enumerate(cabecalho):
        titulo = normalizar(bruto)
        campo = perguntas.get(titulo.rstrip(":").strip()) or perguntas.get(titulo)
        if campo is None:
            if titulo.startswith("carimbo de data"):
                campo = "respondida_em"
            elif "se obriga a ministrar" in titulo:
                campo = "termos"
            elif titulo.startswith("nome completo"):
                campo = "nome"
            elif titulo.startswith("data de nascimento"):
                campo = "nascimento"
            elif titulo.startswith("telefone"):
                campo = "telefone"
            elif titulo.startswith("e-mail") or titulo.startswith("email"):
                campo = "email"
            elif "autorizado" in titulo and "buscar" in titulo:
                campo = "autorizados"
            elif "vencimento da mensalidade" in titulo:
                campo = "vencimento"
            elif titulo.startswith("1. declaro") or titulo.startswith("declaro"):
                campo = "declaracao"
        if campo and campo not in colunas:
            colunas[campo] = indice
            if campo in ("termos", "declaracao"):
                textos[campo] = bruto.strip()
    faltando = [nome for campo, nome in (("respondida_em", "Carimbo de data/hora"),
                                         ("nome", "Nome completo do (a) aluno (a)"))
                if campo not in colunas]
    if faltando:
        raise PlanilhaInvalida(
            "Não encontrei na planilha a(s) coluna(s): " + ", ".join(faltando)
            + ". Use a planilha de respostas do formulário, sem mudar o cabeçalho."
        )
    return colunas, textos


def _data_hora(valor):
    valor = (valor or "").strip()
    try:
        ingenua = _EPOCA_EXCEL + timedelta(days=float(valor))
    except ValueError:
        ingenua = None
        for formato in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%Y-%m-%d %H:%M:%S"):
            try:
                ingenua = datetime.strptime(valor, formato)
                break
            except ValueError:
                continue
        if ingenua is None:
            return None
    return timezone.make_aware(ingenua.replace(microsecond=0))


def _nascimento(valor):
    """(data ou None, texto como foi respondido). Datas impossíveis (ano
    digitado errado, no futuro) ficam só no texto."""
    valor = (valor or "").strip()
    try:
        data = (_EPOCA_EXCEL + timedelta(days=float(valor))).date()
        texto = f"{data:%d/%m/%Y}"
    except (ValueError, OverflowError):
        texto, data = valor, None
        for formato in ("%d/%m/%Y", "%Y-%m-%d"):
            try:
                data = datetime.strptime(valor, formato).date()
                break
            except ValueError:
                continue
    if data is not None and not (date(1900, 1, 1) <= data <= timezone.localdate()):
        data = None
    return data, texto


def _texto_do_telefone(valor):
    """Telefone digitado só com números vira número na planilha e vem em
    notação científica ("2.1988874309E10"): volta para os dígitos."""
    valor = (valor or "").strip()
    if _NUMERO_CIENTIFICO_RE.fullmatch(valor):
        try:
            return str(int(Decimal(valor)))
        except InvalidOperation:
            pass
    return valor


def _whatsapp(valor):
    """O primeiro telefone com DDD escrito na resposta, só dígitos, ou ""."""
    for achado in _TELEFONE_RE.findall(valor or ""):
        numeros = digits(achado)
        if 10 <= len(numeros) <= 13:
            return numeros
    return ""


def _email(valor):
    achado = _EMAIL_RE.search(valor or "")
    return achado.group() if achado else ""


def _dia_vencimento(valor):
    achado = re.search(r"\d{1,2}", valor or "")
    if achado and 1 <= int(achado.group()) <= 31:
        return int(achado.group())
    return None


def _sim_nao(valor):
    texto = normalizar(valor)
    if texto.startswith("sim"):
        return True
    if texto.startswith("nao"):
        return False
    return None


# ---------------------------------------------------------------- relatório
@dataclass
class LinhaImportada:
    numero: int
    nome: str
    acao: str = ""
    aluno: Atleta | None = None
    avisos: list = field(default_factory=list)


@dataclass
class Relatorio:
    linhas: list = field(default_factory=list)
    gravado: bool = False
    termos_copiados: bool = False

    def contar(self, acao):
        return sum(1 for linha in self.linhas if linha.acao == acao)

    @property
    def novos(self):
        return self.contar(NOVO)

    @property
    def existentes(self):
        return self.contar(EXISTENTE)

    @property
    def ignoradas(self):
        return self.contar(JA_IMPORTADA) + self.contar(ERRO)

    @property
    def com_aviso(self):
        return [linha for linha in self.linhas if linha.avisos]


NOVO = "novo"
EXISTENTE = "existente"
JA_IMPORTADA = "ja_importada"
ERRO = "erro"


# --------------------------------------------------------------- importação
def importar_planilha(academia, arquivo, *, turma, cobrar_a_partir_de, gravar=False):
    """Importa as respostas. Devolve um ``Relatorio``; com ``gravar=False``
    nada fica no banco. Levanta ``PlanilhaInvalida`` se o arquivo não for a
    planilha esperada, ou se a turma não tiver valor de mensalidade."""
    if turma.valor_mensalidade is None:
        raise PlanilhaInvalida("A turma escolhida não tem valor de mensalidade definido.")
    linhas = ler_xlsx(arquivo)
    if len(linhas) < 2:
        raise PlanilhaInvalida("A planilha não tem respostas.")
    colunas, textos = _mapear_colunas(linhas[0])

    def valor(linha, campo):
        indice = colunas.get(campo)
        return linha[indice].strip() if indice is not None and indice < len(linha) else ""

    # Em ordem de resposta: se o mesmo aluno respondeu duas vezes, a
    # primeira cria o cadastro e a segunda só acrescenta a ficha.
    respostas = []
    for numero, linha in enumerate(linhas[1:], start=2):
        if any(celula.strip() for celula in linha):
            respostas.append((_data_hora(valor(linha, "respondida_em")), numero, linha))
    respostas.sort(key=lambda r: (r[0] is None, r[0] or timezone.now(), r[1]))

    relatorio = Relatorio(gravado=gravar)
    with transaction.atomic():
        if not academia.termos_matricula.strip() and textos.get("termos"):
            academia.termos_matricula = textos["termos"]
            relatorio.termos_copiados = True
        if not academia.declaracao_matricula.strip() and textos.get("declaracao"):
            academia.declaracao_matricula = textos["declaracao"]
            relatorio.termos_copiados = True
        if relatorio.termos_copiados:
            academia.save(update_fields=["termos_matricula", "declaracao_matricula"])

        alunos = {}
        for aluno in Atleta.objects.filter(academia=academia):
            alunos.setdefault(normalizar(aluno.nome), []).append(aluno)

        for respondida_em, numero, linha in respostas:
            resultado = LinhaImportada(numero=numero, nome=" ".join(valor(linha, "nome").split()))
            relatorio.linhas.append(resultado)
            _importar_linha(
                academia, resultado, respondida_em, lambda campo: valor(linha, campo), textos,
                alunos, turma, cobrar_a_partir_de,
            )
        if not gravar:
            transaction.set_rollback(True)
    return relatorio


def _importar_linha(academia, resultado, respondida_em, valor, textos, alunos, turma, cobrar_a_partir_de):
    from financeiro.services import iniciar_cobranca, proximo_vencimento

    if not resultado.nome:
        resultado.acao = ERRO
        resultado.avisos.append("Sem nome do aluno.")
        return
    if respondida_em is None:
        resultado.acao = ERRO
        resultado.avisos.append("Data da resposta ilegível.")
        return

    nascimento, nascimento_texto = _nascimento(valor("nascimento"))
    telefone = _texto_do_telefone(valor("telefone"))
    whatsapp = _whatsapp(telefone)
    email = _email(valor("email"))
    dia = _dia_vencimento(valor("vencimento"))

    iguais = alunos.get(normalizar(resultado.nome), [])
    if len(iguais) > 1:
        resultado.acao = ERRO
        resultado.avisos.append("Há mais de um aluno com este nome no sistema — importe a ficha à mão.")
        return

    if iguais:
        aluno = iguais[0]
        if FichaMatricula.objects.filter(atleta=aluno, respondida_em=respondida_em).exists():
            resultado.acao = JA_IMPORTADA
            resultado.aluno = aluno
            return
        resultado.acao = EXISTENTE
    else:
        resultado.acao = NOVO
        if nascimento is None:
            resultado.avisos.append(f"Data de nascimento não reconhecida ({nascimento_texto or 'vazia'}): cadastrado sem data.")
        if not whatsapp:
            resultado.avisos.append("Sem WhatsApp com DDD no telefone: as cobranças não chegam até corrigir o cadastro do responsável.")
        responsavel = None
        if whatsapp:
            # Irmãos: o mesmo telefone é o mesmo responsável.
            responsavel = next(
                (r for r in Responsavel.objects.filter(academia=academia) if digits(r.whatsapp) == whatsapp),
                None,
            )
        if responsavel is None:
            # A planilha não tem o nome do responsável; o cadastro pode ser
            # corrigido depois pelo painel.
            responsavel = Responsavel.objects.create(
                academia=academia, nome=f"Responsável de {resultado.nome}"[:150],
                whatsapp=whatsapp, email=email,
            )
        aluno = Atleta.objects.create(
            academia=academia, nome=resultado.nome, data_nascimento=nascimento,
            status="ativo", responsavel_financeiro=responsavel,
        )
        alunos.setdefault(normalizar(resultado.nome), []).append(aluno)
        matricula = Matricula(
            academia=academia, atleta=aluno, unidade=turma.unidade, modalidade=turma.modalidade,
            turma=turma, valor_mensalidade=turma.valor_mensalidade,
            valor_apos_vencimento=turma.valor_apos_vencimento,
            dia_vencimento=dia or turma.dia_vencimento,
            data_inicio=timezone.localdate(), ativo=True,
        )
        matricula.save()
        # Já é aluno: sem taxa de matrícula; a cobrança começa no próximo
        # vencimento a partir da data escolhida na importação.
        iniciar_cobranca(matricula, proximo_vencimento(matricula, a_partir_de=cobrar_a_partir_de))

    resultado.aluno = aluno
    saude = {}
    for campo, _pergunta, campo_detalhe, _detalhe in PERGUNTAS_SAUDE:
        saude[campo] = _sim_nao(valor(campo))
        if campo_detalhe:
            saude[campo_detalhe] = valor(campo_detalhe)
    aceitou_termos = normalizar(valor("termos")).startswith("estou ciente")
    aceitou_declaracao = normalizar(valor("declaracao")).startswith("estou ciente")
    FichaMatricula.objects.create(
        academia=academia,
        atleta=aluno,
        origem=FichaMatricula.PLANILHA,
        respondida_em=respondida_em,
        nome_aluno=valor("nome")[:150],
        data_nascimento_informada=nascimento_texto[:30],
        telefone_contato=telefone[:150],
        email_contato=valor("email")[:254],
        autorizados_buscar=valor("autorizados"),
        vencimento_preferido=dia if dia in (5, 10, 15) else None,
        aceitou_termos=aceitou_termos,
        termos_aceitos=textos.get("termos", "") if aceitou_termos else "",
        aceitou_declaracao=aceitou_declaracao,
        declaracao_aceita=textos.get("declaracao", "") if aceitou_declaracao else "",
        **saude,
    )
    if any(saude.get(campo) for campo, *_ in PERGUNTAS_SAUDE):
        resultado.avisos.append("Respondeu SIM em alguma pergunta de saúde (ver ficha).")

