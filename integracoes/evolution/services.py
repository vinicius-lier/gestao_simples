"""Gestão da instância Evolution de uma academia.

Cada função recebe a ``academia`` (o tenant já resolvido pela view), fala com
a Evolution via ``EvolutionClient`` e reflete o resultado em
``IntegracaoWhatsApp`` (status_conexao / ultimo_status / timestamps / número).

O parsing das respostas da Evolution é isolado nos helpers
``extrair_*`` / ``mapear_status`` / ``normalizar_qrcode`` — nenhum outro lugar
depende dos nomes exatos de campos da API.
"""
from django.utils import timezone

from academias.models import IntegracaoWhatsApp
from integracoes.evolution.client import (
    EvolutionAPIError,
    EvolutionConfigError,
    client_para_config,
)

_ESTADOS_CONECTADO = {"open", "connected", "online", "isonline"}
_ESTADOS_AGUARDANDO = {"connecting", "qr", "qrcode", "pairing", "start", "syncing"}
_ESTADOS_DESCONECTADO = {"close", "closed", "disconnected", "logout", "logged_out", "removed"}


# --------------------------------------------------------------------- parsing
def _cavar(dado, *caminho):
    alvo = dado
    for chave in caminho:
        alvo = alvo.get(chave) if isinstance(alvo, dict) else None
    return alvo


def extrair_estado(resp):
    """String de estado da conexão, encapsulando as formas conhecidas da API."""
    for caminho in (("instance", "state"), ("state",), ("status",), ("instance", "status")):
        valor = _cavar(resp, *caminho)
        if isinstance(valor, str) and valor:
            return valor
    return ""


def mapear_status(estado):
    """Estado bruto da Evolution -> um dos status de IntegracaoWhatsApp."""
    e = (estado or "").strip().lower()
    if e in _ESTADOS_CONECTADO:
        return IntegracaoWhatsApp.STATUS_CONECTADO
    if e in _ESTADOS_AGUARDANDO:
        return IntegracaoWhatsApp.STATUS_AGUARDANDO_QRCODE
    if e in _ESTADOS_DESCONECTADO:
        return IntegracaoWhatsApp.STATUS_DESCONECTADO
    return IntegracaoWhatsApp.STATUS_ERRO


def extrair_numero(resp):
    """Número dono da instância, quando a API o devolve (sem o sufixo JID)."""
    for caminho in (
        ("instance", "owner"),
        ("instance", "number"),
        ("number",),
        ("owner",),
        ("instance", "wuid"),
    ):
        valor = _cavar(resp, *caminho)
        if isinstance(valor, str) and valor:
            return valor.split("@")[0].split(":")[0]
    return ""


def normalizar_qrcode(resp):
    """Devolve ``{"base64": <data-uri|None>, "code": <str|None>}`` aceitando:
    base64 puro, data:image/...;base64,..., ou só o campo ``code``/``pairingCode``."""
    if not isinstance(resp, dict):
        return {"base64": None, "code": None}
    aninhado = resp.get("qrcode") if isinstance(resp.get("qrcode"), dict) else {}
    base64_val = resp.get("base64") or aninhado.get("base64")
    code_val = (
        resp.get("code")
        or aninhado.get("code")
        or resp.get("pairingCode")
        or aninhado.get("pairingCode")
    )
    if base64_val and not str(base64_val).startswith("data:"):
        base64_val = f"data:image/png;base64,{base64_val}"
    return {"base64": base64_val or None, "code": code_val or None}


# ---------------------------------------------------------------- persistência
def persistir_status(config, status, bruto="", numero=None):
    agora = timezone.now()
    campos = ["status_conexao", "ultimo_status", "ultimo_status_em", "atualizado_em"]

    config.status_conexao = status
    config.ultimo_status = (str(bruto) if bruto else status)[:255]
    config.ultimo_status_em = agora

    if status == IntegracaoWhatsApp.STATUS_CONECTADO:
        config.ultima_conexao_em = agora
        campos.append("ultima_conexao_em")

    if numero:
        config.numero_whatsapp = str(numero)[:20]
        campos.append("numero_whatsapp")

    config.save(update_fields=campos)


# ------------------------------------------------------------------- serviços
def _config(academia):
    config = IntegracaoWhatsApp.objects.filter(academia=academia).first()
    if config is None:
        raise EvolutionConfigError(
            "Esta academia ainda não tem integração de WhatsApp configurada."
        )
    if config.provider != IntegracaoWhatsApp.PROVIDER_EVOLUTION:
        raise EvolutionConfigError(
            "A integração de WhatsApp desta academia não usa a Evolution API."
        )
    return config


def criar_instancia(academia):
    config = _config(academia)
    client = client_para_config(config)
    try:
        resp = client.criar_instancia(numero=config.numero_whatsapp or None)
    except EvolutionAPIError as exc:
        persistir_status(config, IntegracaoWhatsApp.STATUS_ERRO, bruto=str(exc))
        raise
    persistir_status(
        config, IntegracaoWhatsApp.STATUS_AGUARDANDO_QRCODE, bruto="instância criada"
    )
    return {"qrcode": normalizar_qrcode(resp), "raw": resp}


def consultar_status(academia):
    config = _config(academia)
    client = client_para_config(config)
    try:
        resp = client.buscar_estado_conexao()
    except EvolutionAPIError as exc:
        persistir_status(config, IntegracaoWhatsApp.STATUS_ERRO, bruto=str(exc))
        raise
    estado = extrair_estado(resp)
    status = mapear_status(estado)
    persistir_status(
        config, status, bruto=estado or "sem estado", numero=extrair_numero(resp)
    )
    return {"status": status, "estado": estado, "numero": config.numero_whatsapp, "raw": resp}


def gerar_qrcode(academia):
    config = _config(academia)
    client = client_para_config(config)
    try:
        resp = client.obter_qrcode()
    except EvolutionAPIError as exc:
        persistir_status(config, IntegracaoWhatsApp.STATUS_ERRO, bruto=str(exc))
        raise

    return _registrar_qrcode(config, resp)


def _registrar_qrcode(config, resp):
    """Reflete a resposta do /instance/connect no status e devolve o QR.
    O QR code NÃO é persistido — vive só nesta resposta."""
    estado = extrair_estado(resp)
    if estado and mapear_status(estado) == IntegracaoWhatsApp.STATUS_CONECTADO:
        persistir_status(
            config,
            IntegracaoWhatsApp.STATUS_CONECTADO,
            bruto=estado,
            numero=extrair_numero(resp),
        )
    else:
        persistir_status(
            config, IntegracaoWhatsApp.STATUS_AGUARDANDO_QRCODE, bruto="qrcode gerado"
        )
    return normalizar_qrcode(resp)


def nome_da_instancia(academia):
    """Nome da instância na Evolution da plataforma: único por academia."""
    return f"academia-{academia.pk}"


def conectar(academia, numero):
    """O fluxo da tela Configurações → WhatsApp: a academia informa o número
    e recebe o QR Code para ler com o celular. Liga a Evolution para a
    academia, cria a instância na primeira vez e, se o número mudou, derruba
    a sessão do número antigo. Já conectado com o mesmo número: devolve
    ``{"conectado": True}`` sem QR."""
    from integracoes.whatsapp import normalizar_telefone

    numero = normalizar_telefone(numero)
    if not 12 <= len(numero) <= 13:
        raise EvolutionConfigError("Informe o número do WhatsApp com DDD.")

    config, _ = IntegracaoWhatsApp.objects.get_or_create(academia=academia)
    trocou = bool(config.numero_whatsapp) and config.numero_whatsapp != numero
    config.provider = IntegracaoWhatsApp.PROVIDER_EVOLUTION
    config.evolution_instance_name = config.evolution_instance_name or nome_da_instancia(academia)
    config.numero_whatsapp = numero
    config.save(update_fields=["provider", "evolution_instance_name", "numero_whatsapp", "atualizado_em"])

    client = client_para_config(config)
    try:
        if trocou:
            try:
                client.logout()
            except EvolutionAPIError:
                pass  # a sessão antiga pode já estar desconectada
        try:
            estado = extrair_estado(client.buscar_estado_conexao())
        except EvolutionAPIError as exc:
            if exc.status_code != 404:
                raise
            # Primeira vez: a instância ainda não existe na Evolution.
            criada = normalizar_qrcode(client.criar_instancia(numero=numero))
            if criada["base64"] or criada["code"]:
                persistir_status(
                    config, IntegracaoWhatsApp.STATUS_AGUARDANDO_QRCODE, bruto="instância criada"
                )
                return criada
        else:
            if not trocou and mapear_status(estado) == IntegracaoWhatsApp.STATUS_CONECTADO:
                persistir_status(config, IntegracaoWhatsApp.STATUS_CONECTADO, bruto=estado)
                return {"base64": None, "code": None, "conectado": True}
        resp = client.obter_qrcode()
    except EvolutionAPIError as exc:
        persistir_status(config, IntegracaoWhatsApp.STATUS_ERRO, bruto=str(exc))
        raise
    return _registrar_qrcode(config, resp)


def desconectar(academia):
    config = _config(academia)
    client = client_para_config(config)
    try:
        resp = client.logout()
    except EvolutionAPIError as exc:
        persistir_status(config, IntegracaoWhatsApp.STATUS_ERRO, bruto=str(exc))
        raise
    persistir_status(config, IntegracaoWhatsApp.STATUS_DESCONECTADO, bruto="logout")
    return {"status": IntegracaoWhatsApp.STATUS_DESCONECTADO, "raw": resp}


def trocar_numero(academia, novo_numero):
    from integracoes.whatsapp import normalizar_telefone

    numero = normalizar_telefone(novo_numero)
    if not numero:
        raise EvolutionConfigError("Informe um número de WhatsApp válido.")

    config = _config(academia)
    client = client_para_config(config)

    # Derruba a sessão atual: o número novo exige um novo pareamento (QR).
    try:
        client.logout()
    except EvolutionAPIError:
        pass  # instância já pode estar desconectada — segue

    persistir_status(
        config,
        IntegracaoWhatsApp.STATUS_DESCONECTADO,
        bruto="troca de número",
        numero=numero,
    )
    return {"numero": config.numero_whatsapp, "status": IntegracaoWhatsApp.STATUS_DESCONECTADO}
