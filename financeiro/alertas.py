"""Alertas para quem opera a plataforma (não para a academia).

Hoje o alerta vai para o log, no logger ``gestao.alertas`` com nível ERROR
(no Coolify, aparece nos logs do contêiner). A situação também fica
registrada no banco: repasses em "Requer atenção" aparecem no /admin/ e num
aviso do portal para superusuários.

Ponto de integração: para Zabbix, Discord, e-mail etc., acrescente o envio
dentro de ``alertar_plataforma`` — é o único lugar que dispara alertas.
"""
import logging

logger = logging.getLogger("gestao.alertas")


def alertar_plataforma(assunto, detalhe="", **contexto):
    """Registra um alerta operacional. Nunca inclua credenciais em
    ``detalhe`` ou ``contexto``."""
    extras = " ".join(f"{chave}={valor}" for chave, valor in sorted(contexto.items()))
    logger.error("ALERTA: %s. %s %s", assunto, detalhe, extras)
