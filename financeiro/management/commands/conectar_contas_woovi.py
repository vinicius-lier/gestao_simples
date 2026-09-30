from django.core.management.base import BaseCommand

from financeiro.models import ContaRecebimento
from integracoes.woovi.contas import atualizar_onboarding, automacao_disponivel, conectar_automaticamente
from integracoes.woovi.exceptions import WooviError


class Command(BaseCommand):
    help = (
        "Confere na Woovi o cadastro (KYC) das contas próprias ainda não "
        "conectadas e, quando aprovado, conclui a conexão pela Partner API: "
        "cria a credencial da afiliada, guarda-a cifrada e ativa a conta. "
        "Idempotente e seguro de repetir. Não imprime credenciais. Pode ser "
        "agendado (por exemplo, a cada hora); a tela de Recebimento faz o "
        "mesmo quando a academia clica em \"Atualizar situação do cadastro\"."
    )

    def handle(self, *args, **options):
        pendentes = ContaRecebimento.objects.filter(
            modelo_recebimento=ContaRecebimento.CONTA_PROPRIA, conectada_em__isnull=True,
        ).exclude(onboarding_url="").select_related("academia")
        conectadas = aguardando = falhas = 0
        for conta in pendentes:
            if not automacao_disponivel(conta):
                continue
            try:
                atualizar_onboarding(conta)
                if conta.onboarding_status != "APPROVED":
                    aguardando += 1
                    continue
                conectar_automaticamente(conta)
                conectadas += 1
            except (WooviError, ValueError) as erro:
                falhas += 1
                self.stderr.write(f"Conta {conta.pk}: conexão não concluída ({type(erro).__name__}).")
        self.stdout.write(f"{conectadas} conectada(s), {aguardando} aguardando aprovação, {falhas} com falha.")
