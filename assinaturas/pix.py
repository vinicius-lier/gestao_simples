"""Pix "copia e cola" estático (BR Code, padrão EMV do Banco Central) para a
chave da plataforma, com o valor da fatura. Gerado aqui: não depende de
provedor de pagamento, e o dinheiro cai direto na conta dona da chave."""
import re
import unicodedata


def _campo(identificador, valor):
    return f"{identificador}{len(valor):02d}{valor}"


def _ascii(texto, limite):
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9 ]", "", sem_acento).strip()[:limite]


def crc16(dados):
    """CRC16-CCITT (polinômio 0x1021, início 0xFFFF), exigido pelo BR Code."""
    crc = 0xFFFF
    for byte in dados.encode("utf-8"):
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)
            crc &= 0xFFFF
    return f"{crc:04X}"


def br_code_estatico(chave, nome, cidade, valor=None, txid="***"):
    """BR Code do Pix. ``valor`` em reais (None = quem paga digita);
    ``txid`` identifica o pagamento no extrato (só letras e números)."""
    conta = _campo("00", "br.gov.bcb.pix") + _campo("01", chave.strip())
    payload = (
        _campo("00", "01")
        + _campo("26", conta)
        + _campo("52", "0000")
        + _campo("53", "986")
        + (_campo("54", f"{valor:.2f}") if valor is not None else "")
        + _campo("58", "BR")
        + _campo("59", _ascii(nome, 25))
        + _campo("60", _ascii(cidade, 15))
        + _campo("62", _campo("05", re.sub(r"[^A-Za-z0-9*]", "", txid)[:25] or "***"))
        + "6304"
    )
    return payload + crc16(payload)
