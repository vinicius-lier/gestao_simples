"""Handler de log que manda ao Discord os erros 500 (logger ``django.request``)
e os alertas da plataforma (``gestao.alertas``, ver financeiro/alertas.py),
explicados: o que aconteceu, o que significa e o que fazer.

Carregado pelo LOGGING do settings antes dos apps: nada de models no import.
"""
import logging
import os
import re
import traceback

# Segmentos longos e aleatórios da URL são links de acesso de uso único
# (portal do responsável, convite de matrícula): não podem ir para o Discord.
_TOKEN_NA_ROTA = re.compile(r"[A-Za-z0-9_-]{20,}")

# Assunto do alerta (início) -> (o que isso significa, o que fazer).
EXPLICACOES = {
    "Repasse para a chave Pix da academia requer atenção": (
        "Uma família pagou, mas o sistema não conseguiu transferir o dinheiro para a chave Pix da "
        "escola, mesmo depois de várias tentativas. O dinheiro está guardado na conta da plataforma "
        "na Woovi — nada se perdeu.",
        "Abra /admin/financeiro/repasse/ e veja o motivo na coluna **Erro**. Se a chave Pix estiver "
        "errada, peça à escola para cadastrar outra em Configurações → Recebimento. Depois selecione "
        "o repasse e use a ação **Tentar o repasse de novo**.",
    ),
    "Chave Pix cadastrada com transferência bloqueada": (
        "A escola cadastrou uma chave Pix que a Woovi não aceita como destino de transferência "
        "(chave inválida ou com restrição). Os pagamentos entram, mas não chegam à escola.",
        "Peça à escola outra chave Pix — de preferência o CNPJ ou o e-mail da conta da escola — em "
        "Configurações → Recebimento.",
    ),
    "Mensalidade paga em dobro": (
        "Uma família pagou por Pix uma mensalidade que já estava marcada como paga (por exemplo, "
        "pagou em dinheiro na escola e depois pelo Pix). O valor entrou duas vezes.",
        "Combine com a escola e a família: devolver o valor ou usar como crédito na próxima "
        "mensalidade. A mensalidade está no admin: Financeiro → Mensalidades (número na Referência).",
    ),
    "Pix pago de mensalidade": (
        "Uma família pagou por Pix uma mensalidade que tinha sido cancelada ou isentada.",
        "Confirme com a escola se a mensalidade devia mesmo estar cancelada/isenta. Se sim, devolva "
        "o valor à família; se não, reabra a mensalidade no admin.",
    ),
}
EXPLICACAO_PADRAO = (
    "Uma situação no financeiro que o sistema não resolve sozinho.",
    "Veja os detalhes no admin (Financeiro) usando a Referência abaixo.",
)


class AlertaDiscordHandler(logging.Handler):
    def emit(self, record):
        try:
            from django.conf import settings

            from .alertas import enviar_alerta

            titulo, texto, chave, campos = resumir(record)
            enviar_alerta(
                titulo, texto, chave, campos=campos,
                em_segundo_plano=getattr(settings, "ALERTAS_EM_SEGUNDO_PLANO", True),
            )
        except Exception:
            self.handleError(record)


def resumir(record):
    """(título, texto, chave de repetição, seções) do alerta."""
    if record.name == "gestao.alertas":
        return _resumir_alerta_da_plataforma(record)
    return _resumir_erro(record)


def _resumir_alerta_da_plataforma(record):
    args = record.args if isinstance(record.args, tuple) else ()
    assunto = str(args[0]) if args else record.getMessage()
    detalhe = str(args[1]).strip() if len(args) > 1 else ""
    referencia = str(args[2]).strip() if len(args) > 2 else ""
    significa, fazer = next(
        (explicacao for inicio, explicacao in EXPLICACOES.items() if assunto.startswith(inicio)),
        EXPLICACAO_PADRAO,
    )
    campos = [("O que isso significa", significa), ("O que fazer", fazer)]
    if detalhe:
        campos.append(("Detalhe", detalhe[:500]))
    if referencia:
        campos.append(("Referência", f"`{referencia[:300]}`"))
    return f"🔔 {assunto[:240]}", "Alerta do financeiro do sistema.", f"alerta:{assunto[:150]}", campos


def _resumir_erro(record):
    request = getattr(record, "request", None)
    rota = f"{request.method} {_TOKEN_NA_ROTA.sub('[token]', request.path)}" if request is not None else "?"
    tipo, detalhe, onde = "Erro", record.getMessage(), "?"
    if record.exc_info and record.exc_info[1] is not None:
        excecao = record.exc_info[1]
        tipo = type(excecao).__name__
        detalhe = str(excecao)
        onde = _linha_do_projeto(record.exc_info[2])
    texto = (
        f"Alguém abriu **{rota}** e o sistema falhou: em vez da tela, a pessoa viu uma página de "
        "erro (HTTP 500)."
    )
    campos = [
        ("O que fazer",
         "Se foi uma vez só, pode ignorar. Se repetir, abra o Coolify → aplicação **gestao_simples** "
         "→ **Logs**, procure pelo horário deste aviso e envie o trecho do erro para correção."),
        ("Detalhe técnico", f"`{tipo}`: {detalhe[:300]}\nOnde no código: `{onde}`"),
    ]
    return f"🚨 Erro no sistema: {tipo}", texto, f"erro:{tipo}:{onde}", campos


def _linha_do_projeto(tb):
    """Última linha do traceback que é código do projeto (não de biblioteca)."""
    from django.conf import settings

    raiz = str(settings.BASE_DIR)
    quadros = traceback.extract_tb(tb)
    do_projeto = [
        q for q in quadros
        if q.filename.startswith(raiz) and "site-packages" not in q.filename and ".venv" not in q.filename
    ]
    quadro = (do_projeto or quadros or [None])[-1]
    if quadro is None:
        return "?"
    caminho = os.path.relpath(quadro.filename, raiz) if quadro.filename.startswith(raiz) else quadro.filename
    return f"{caminho.replace(os.sep, '/')}:{quadro.lineno}"
