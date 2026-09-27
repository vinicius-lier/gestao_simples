from django.core.management.base import BaseCommand

from financeiro.lembretes import enviar_lembretes
from financeiro.models import Mensalidade
from financeiro.services import gerar_mensalidades_do_dia
from integracoes.woovi.repasses import processar_repasses


class Command(BaseCommand):
    help = (
        "Rotina diária de cobrança: gera as mensalidades do mês (e antecipa "
        "as do mês seguinte que vencem em até 7 dias), marca as vencidas e "
        "envia os lembretes pelo WhatsApp — 5 dias antes do vencimento, "
        "1 dia antes, no dia e quando a mensalidade fica atrasada. Cada "
        "estágio é enviado no máximo uma vez por mensalidade. Agende para "
        "rodar 1x por dia (systemd timer/cron/Agendador de Tarefas do Windows)."
    )

    def handle(self, *args, **options):
        geradas = gerar_mensalidades_do_dia()
        self.stdout.write(f"{geradas['criadas']} mensalidade(s) gerada(s).")
        Mensalidade.objects.marcar_vencidas()
        enviados = enviar_lembretes()
        self.stdout.write(self.style.SUCCESS(f"{len(enviados)} lembrete(s) enviado(s)."))

        # Assinatura do sistema: fatura do mês de cada academia e aviso das
        # vencidas para a plataforma (Discord).
        from assinaturas.services import avisar_faturas_vencidas, gerar_faturas

        self.stdout.write(f"{gerar_faturas()} fatura(s) da assinatura do sistema gerada(s).")
        avisar_faturas_vencidas()

        # Rede de segurança dos repasses Pix (o site os processa sozinho
        # quando o Pix entra): pega algum que tenha ficado para trás.
        repasses = processar_repasses()
        if repasses["processados"] or repasses["conferidos"]:
            self.stdout.write(f"{repasses['processados']} repasse(s) Pix retomado(s).")
