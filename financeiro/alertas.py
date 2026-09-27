"""Alertas para quem opera a plataforma (não para a academia).

O alerta vai para o logger ``gestao.alertas`` com nível ERROR: aparece nos
logs do contêiner (Coolify) e, com ALERTAS_DISCORD_WEBHOOK_URL configurado,
no canal do Discord (ver monitoramento/log.py). A situação também fica
registrada no banco: repasses em "Requer atenção" aparecem no /admin/ e num
aviso do portal para superusuários.
"""
import logging

logger = logging.getLogger("gestao.alertas")


def alertar_plataforma(assunto, detalhe="", **contexto):
    """Registra um alerta operacional. Nunca inclua credenciais em
    ``detalhe`` ou ``contexto``."""
    extras = " ".join(f"{chave}={valor}" for chave, valor in sorted(contexto.items()))
    logger.error("ALERTA: %s. %s %s", assunto, detalhe, extras)
