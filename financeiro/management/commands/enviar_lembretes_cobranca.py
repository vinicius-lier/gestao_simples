from django.core.management.base import BaseCommand

from financeiro.lembretes import enviar_lembretes
from financeiro.models import Mensalidade


class Command(BaseCommand):
    help = (
        "Envia lembretes de cobrança pelo WhatsApp: 5 dias antes do "
        "vencimento, 1 dia antes, no dia e quando a mensalidade fica "
        "atrasada. Cada estágio é enviado no máximo uma vez por "
        "mensalidade. Agende para rodar 1x por dia (cron/Agendador de "
        "Tarefas do Windows)."
    )

    def handle(self, *args, **options):
        Mensalidade.objects.marcar_vencidas()
        enviados = enviar_lembretes()
        self.stdout.write(self.style.SUCCESS(f"{len(enviados)} lembrete(s) enviado(s)."))
