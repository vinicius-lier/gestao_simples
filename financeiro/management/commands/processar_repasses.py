from django.core.management.base import BaseCommand

from integracoes.woovi.repasses import processar_repasses


class Command(BaseCommand):
    help = (
        "Transfere para a chave Pix de cada academia o saldo recebido pelos "
        "Pix pagos (repasses pendentes e novas tentativas vencidas) e confere "
        "os repasses sem confirmação. Não precisa de agendamento: o site faz "
        "isso sozinho quando entra um Pix, e a rotina diária dá uma passada. "
        "Use para rodar na hora, à mão. Seguro rodar em paralelo: cada "
        "repasse é reivindicado atomicamente."
    )

    def handle(self, *args, **options):
        resultado = processar_repasses()
        if resultado["processados"] or resultado["conferidos"]:
            self.stdout.write(
                f"{resultado['processados']} repasse(s) processado(s), "
                f"{resultado['conferidos']} conferido(s) no extrato."
            )
