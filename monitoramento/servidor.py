"""Uso de RAM e disco da VPS. O contêiner não limita memória e grava no disco
do servidor, então /proc/meminfo e a raiz "/" mostram os números da máquina."""
import shutil
from pathlib import Path


def uso_ram(caminho="/proc/meminfo"):
    """% da RAM em uso (1 − MemAvailable/MemTotal), ou None fora do Linux."""
    try:
        linhas = Path(caminho).read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    valores = {}
    for linha in linhas:
        nome, _, resto = linha.partition(":")
        partes = resto.split()
        if partes and partes[0].isdigit():
            valores[nome.strip()] = int(partes[0])
    total, disponivel = valores.get("MemTotal"), valores.get("MemAvailable")
    if not total or disponivel is None:
        return None
    return 100 * (1 - disponivel / total)


def uso_disco(caminho="/"):
    """% do disco em uso."""
    uso = shutil.disk_usage(caminho)
    return 100 * uso.used / uso.total
