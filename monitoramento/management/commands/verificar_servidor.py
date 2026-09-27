from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand

from monitoramento.alertas import enviar_alerta
from monitoramento.servidor import uso_disco, uso_ram

EXPLICACOES = {
    "RAM": (
        "Servidor com pouca memória RAM",
        "Quando a memória acaba, o sistema fica lento, páginas demoram ou dão erro, e os serviços "
        "podem ser reiniciados à força.",
        "No Coolify, abra **Servers → localhost → Metrics** e veja qual serviço está usando mais "
        "memória; reiniciar esse serviço costuma resolver na hora. Se acontecer com frequência, "
        "aumente o plano (RAM) da VPS.",
    ),
    "Disco": (
        "Disco do servidor quase cheio",
        "Com o disco cheio, o banco de dados para de gravar (cadastros e pagamentos falham), os "
        "backups deixam de ser feitos e novos deploys não sobem.",
        "No Coolify, abra **Servers → localhost → Docker Cleanup** e rode a limpeza de imagens "
        "antigas. Se não bastar, apague backups antigos ou aumente o disco da VPS.",
    ),
}


class Command(BaseCommand):
    help = (
        "Confere RAM e disco da VPS e avisa no Discord quando passam do limite "
        "(MONITORAMENTO_LIMITE_RAM / MONITORAMENTO_LIMITE_DISCO). Agende a cada "
        "5 minutos; o mesmo aviso se repete no máximo a cada 6 horas."
    )

    def handle(self, *args, **options):
        ram, disco = uso_ram(), uso_disco()
        resumo = " | ".join(
            f"{nome} {uso:.0f}%" for nome, uso in (("RAM", ram), ("Disco", disco)) if uso is not None
        )
        for nome, uso, limite, chave in (
            ("RAM", ram, settings.MONITORAMENTO_LIMITE_RAM, "servidor:ram"),
            ("Disco", disco, settings.MONITORAMENTO_LIMITE_DISCO, "servidor:disco"),
        ):
            if uso is not None and uso >= limite:
                titulo, significa, fazer = EXPLICACOES[nome]
                enviar_alerta(
                    f"⚠️ {titulo} ({uso:.0f}% em uso)",
                    f"{nome} da VPS em **{uso:.0f}%**, acima do limite de {limite}%.\nAgora: {resumo}.",
                    chave,
                    campos=[("O que isso significa", significa), ("O que fazer", fazer)],
                    nivel="aviso",
                    intervalo=timedelta(hours=6),
                )
        self.stdout.write(resumo or "Métricas do servidor indisponíveis.")
