"""Executa migrations reais em banco de teste com histórico financeiro."""
from datetime import date

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase, override_settings

from assinaturas.models import Assinatura, FaturaAssinatura
from financeiro.models import Repasse
from tests.woovi_base import CenarioWoovi


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class MigracaoContasWooviTests(CenarioWoovi, TransactionTestCase):
    def test_migracao_preserva_historico_e_fixa_origem_legada(self):
        self.criar_cenario()
        charge = self.cobranca(transaction_id="tx-historico", link_pagamento="https://woovi.com/pay/historico")
        repasse = Repasse.objects.create(academia=self.academia, conta_recebimento=self.conta, pix_key_destino=self.conta.pix_key)
        assinatura = Assinatura.objects.create(academia=self.academia, valor_mensal="99.00", inicio=date(2026, 1, 1))
        fatura = FaturaAssinatura.objects.create(assinatura=assinatura, competencia=date(2026, 9, 1), valor="99.00", vencimento=date(2026, 9, 10), correlation_id="assinatura-antiga", br_code="PIX-SISTEMA-ANTIGO")
        executor = MigrationExecutor(connection)
        finais = executor.loader.graph.leaf_nodes()
        anteriores = [("financeiro", "0009_taxa_matricula_e_valor_apos_vencimento"), ("assinaturas", "0002_status_woovi_e_tolerancia")]
        try:
            executor.migrate(anteriores)
            # Outra credencial pode já estar configurada na implantação:
            # ela NÃO deve ser aplicada a registros antigos pela migration.
            with override_settings(WOOVI_PLATAFORMA_APP_ID="nova-plataforma", WOOVI_BASE_URL="https://api.woovi-sandbox.com"):
                executor = MigrationExecutor(connection)
                executor.migrate(finais)
            charge.refresh_from_db()
            self.conta.refresh_from_db()
            repasse.refresh_from_db()
            fatura.refresh_from_db()
            self.assertEqual(self.conta.modelo_recebimento, "legado_subconta")
            self.assertEqual(self.conta.credencial_ref, "WOOVI_APP_ID")
            self.assertEqual(self.conta.api_base_url, "https://api.woovi-sandbox.com")
            self.assertEqual(len(self.conta.credencial_fingerprint), 64)
            self.assertEqual(charge.modelo_recebimento, "legado_subconta")
            self.assertEqual(charge.conta_recebimento_id, self.conta.pk)
            self.assertEqual((charge.correlation_id, charge.transaction_id, charge.br_code),
                             ("mensalidade-1-abc", "tx-historico", "00020126PIX"))
            self.assertEqual(charge.link_pagamento, "https://woovi.com/pay/historico")
            self.assertEqual(repasse.conta_recebimento_id, self.conta.pk)
            self.assertEqual(repasse.status, Repasse.PENDENTE)
            self.assertEqual(fatura.credencial_ref, "WOOVI_APP_ID")
            self.assertEqual(fatura.api_base_url, "https://api.woovi-sandbox.com")
            self.assertEqual(fatura.br_code, "PIX-SISTEMA-ANTIGO")
        finally:
            MigrationExecutor(connection).migrate(finais)
