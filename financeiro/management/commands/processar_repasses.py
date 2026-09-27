from django.core.management.base import BaseCommand

from integracoes.woovi.repasses import processar_repasses


class Command(BaseCommand):
    help = (
        "Transfere para a chave Pix de cada academia o saldo recebido pelos "
        "Pix pagos (repasses pendentes e novas tentativas vencidas) e confere "
        "os repasses sem confirmação. Agende a cada minuto (Coolify: "
        "Scheduled Task '* * * * *'). Seguro rodar em paralelo: cada repasse "
        "é reivindicado atomicamente."
    )

    def handle(self, *args, **options):
        resultado = processar_repasses()
        if resultado["processados"] or resultado["conferidos"]:
            self.stdout.write(
                f"{resultado['processados']} repasse(s) processado(s), "
                f"{resultado['conferidos']} conferido(s) no extrato."
            )
