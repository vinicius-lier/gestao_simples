"""Validação e normalização de chaves Pix (formato do DICT).

O tipo vem escolhido pela pessoa: 11 dígitos podem ser CPF ou celular sem
DDI, então o tipo não é adivinhado. Mensagens em linguagem de usuário.
"""
import re

from django.core.exceptions import ValidationError
from django.core.validators import validate_email

from financeiro.models import ContaRecebimento

_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


class ChavePixInvalida(ValueError):
    pass


def _digitos(valor):
    return re.sub(r"\D", "", valor or "")


def _cpf_valido(cpf):
    if len(cpf) != 11 or cpf == cpf[0] * 11:
        return False
    for tamanho in (9, 10):
        soma = sum(int(cpf[i]) * (tamanho + 1 - i) for i in range(tamanho))
        digito = (soma * 10 % 11) % 10
        if digito != int(cpf[tamanho]):
            return False
    return True


def _cnpj_valido(cnpj):
    if len(cnpj) != 14 or cnpj == cnpj[0] * 14:
        return False
    pesos = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    for tamanho in (12, 13):
        soma = sum(int(cnpj[i]) * pesos[i + 13 - tamanho] for i in range(tamanho))
        resto = soma % 11
        digito = 0 if resto < 2 else 11 - resto
        if digito != int(cnpj[tamanho]):
            return False
    return True


def normalizar_chave_pix(tipo, valor):
    """Devolve a chave no formato do DICT, ou levanta ChavePixInvalida."""
    valor = (valor or "").strip()
    if not valor:
        raise ChavePixInvalida("Informe a chave Pix.")

    if tipo == ContaRecebimento.CPF:
        cpf = _digitos(valor)
        if not _cpf_valido(cpf):
            raise ChavePixInvalida("CPF inválido. Confira os números.")
        return cpf

    if tipo == ContaRecebimento.CNPJ:
        cnpj = _digitos(valor)
        if not _cnpj_valido(cnpj):
            raise ChavePixInvalida("CNPJ inválido. Confira os números.")
        return cnpj

    if tipo == ContaRecebimento.EMAIL:
        email = valor.lower()
        try:
            validate_email(email)
        except ValidationError:
            raise ChavePixInvalida("E-mail inválido.") from None
        if len(email) > 77:
            raise ChavePixInvalida("E-mail longo demais para uma chave Pix.")
        return email

    if tipo == ContaRecebimento.TELEFONE:
        telefone = _digitos(valor)
        if len(telefone) in (10, 11):
            telefone = "55" + telefone
        if not (telefone.startswith("55") and len(telefone) in (12, 13)):
            raise ChavePixInvalida("Celular inválido. Use DDD + número, ex.: (21) 99999-8888.")
        return "+" + telefone

    if tipo == ContaRecebimento.ALEATORIA:
        chave = valor.lower()
        if not _UUID.match(chave):
            raise ChavePixInvalida("Chave aleatória inválida. Copie a chave completa do app do banco.")
        return chave

    raise ChavePixInvalida("Escolha o tipo da chave Pix.")
