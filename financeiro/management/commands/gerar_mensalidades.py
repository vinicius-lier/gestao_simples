from datetime import date

from django.core.management.base import BaseCommand

from financeiro.services import gerar_mensalidades


class Command(BaseCommand):
    help = "Gera mensalidades das matrículas ativas"

    def add_arguments(self, parser):
        parser.add_argument(
            "--ano",
            type=int,
        )

        parser.add_argument(
            "--mes",
            type=int,
        )

    def handle(self, *args, **options):
        hoje = date.today()

        ano = options["ano"] or hoje.year
        mes = options["mes"] or hoje.month

        resultado = gerar_mensalidades(
            ano=ano,
            mes=mes,
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Mensalidades criadas: {resultado['criadas']}"
            )
        )

        self.stdout.write(
            f"Mensalidades já existentes: {resultado['existentes']}"
        )