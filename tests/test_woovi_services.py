from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from academias.models import Academia
from atletas.models import Atleta, Responsavel
from financeiro.models import Mensalidade
from integracoes.woovi.client import WooviAPIError
from integracoes.woovi.services import (
    confirmar_pagamento_pix,
    garantir_cobranca_pix,
    remover_cobranca_pix,
    valor_em_centavos,
)
from matriculas.models import Matricula
from modalidades.models import Modalidade


def cobranca_criada(**kw):
    return {
        "correlationID": kw["correlation_id"],
        "brCode": "00020126PIX",
        "qrCodeImage": "https://api.woovi.com/openpix/charge/brcode/image/x.png",
        "paymentLinkUrl": "https://woovi.com/pay/x",
        "expiresDate": "2999-01-01T00:00:00.000Z",
    }


@patch("integracoes.woovi.services.WooviClient")
class WooviServicesTests(TestCase):
    def setUp(self):
        academia = Academia.objects.create(nome="Academia", cnpj="WS1")
        self.responsavel = Responsavel.objects.create(
            academia=academia, nome="Maria", cpf="123.456.789-00", whatsapp="(21) 99999-8888",
        )
        atleta = Atleta.objects.create(academia=academia, nome="Ana", responsavel_financeiro=self.responsavel)
        matricula = Matricula.objects.create(
            academia=academia, atleta=atleta, modalidade=Modalidade.objects.create(academia=academia, nome="Judô"),
            valor_mensalidade=Decimal("135.50"), dia_vencimento=10, data_inicio=date(2026, 1, 1),
        )
        self.mensalidade = Mensalidade.objects.create(
            academia=academia, matricula=matricula, competencia=date(2026, 9, 1),
            valor=Decimal("135.50"), vencimento=date(2026, 9, 10), status="pendente",
        )

    def test_valor_em_centavos(self, _mock):
        self.assertEqual(valor_em_centavos(Decimal("135.50")), 13550)
        self.assertEqual(valor_em_centavos(Decimal("0.01")), 1)

    def test_gera_pix_com_valor_cliente_e_validade(self, mock_client_class):
        mock_client_class.return_value.criar_cobranca.side_effect = cobranca_criada

        garantir_cobranca_pix(self.mensalidade)

        kwargs = mock_client_class.return_value.criar_cobranca.call_args.kwargs
        self.assertEqual(kwargs["valor_centavos"], 13550)
        self.assertEqual(kwargs["comentario"], "Mensalidade 09/2026 - Ana")
        self.assertEqual(kwargs["expira_em_segundos"], 30 * 24 * 60 * 60)
        self.assertEqual(kwargs["cliente"], {"name": "Maria", "taxID": "12345678900", "phone": "5521999998888"})
        self.mensalidade.refresh_from_db()
        self.assertEqual(self.mensalidade.woovi_br_code, "00020126PIX")
        self.assertEqual(self.mensalidade.woovi_link_pagamento, "https://woovi.com/pay/x")
        self.assertEqual(self.mensalidade.woovi_expira_em.year, 2999)

    def test_sem_responsavel_gera_pix_sem_cliente(self, mock_client_class):
        mock_client_class.return_value.criar_cobranca.side_effect = cobranca_criada
        atleta = self.mensalidade.matricula.atleta
        atleta.responsavel_financeiro = None
        atleta.save(update_fields=["responsavel_financeiro"])

        garantir_cobranca_pix(self.mensalidade)

        self.assertIsNone(mock_client_class.return_value.criar_cobranca.call_args.kwargs["cliente"])

    def test_pix_vigente_nao_chama_a_woovi_de_novo(self, mock_client_class):
        mock_client_class.return_value.criar_cobranca.side_effect = cobranca_criada

        garantir_cobranca_pix(self.mensalidade)
        garantir_cobranca_pix(Mensalidade.objects.get(pk=self.mensalidade.pk))

        mock_client_class.return_value.criar_cobranca.assert_called_once()

    def test_pix_perto_de_expirar_e_trocado_por_outro(self, mock_client_class):
        mock_client_class.return_value.criar_cobranca.side_effect = cobranca_criada
        self.mensalidade.woovi_correlation_id = "mensalidade-antigo"
        self.mensalidade.woovi_expira_em = timezone.now() + timedelta(minutes=10)
        self.mensalidade.save()

        garantir_cobranca_pix(self.mensalidade)

        self.mensalidade.refresh_from_db()
        self.assertNotEqual(self.mensalidade.woovi_correlation_id, "mensalidade-antigo")

    def test_mensalidade_paga_nao_gera_pix(self, mock_client_class):
        self.mensalidade.status = "paga"
        self.mensalidade.save(update_fields=["status"])

        with self.assertRaisesMessage(ValueError, "não pode ser cobrada"):
            garantir_cobranca_pix(self.mensalidade)
        mock_client_class.assert_not_called()

    def test_resposta_sem_br_code_nao_grava_nada(self, mock_client_class):
        mock_client_class.return_value.criar_cobranca.return_value = {"correlationID": "c1"}

        with self.assertRaises(WooviAPIError):
            garantir_cobranca_pix(self.mensalidade)

        self.mensalidade.refresh_from_db()
        self.assertEqual(self.mensalidade.woovi_correlation_id, "")

    def test_remover_so_chama_a_woovi_com_pix_vigente(self, mock_client_class):
        remover_cobranca_pix(self.mensalidade)
        mock_client_class.assert_not_called()

        self.mensalidade.woovi_correlation_id = "c1"
        self.mensalidade.woovi_expira_em = timezone.now() + timedelta(days=1)
        remover_cobranca_pix(self.mensalidade)
        mock_client_class.return_value.remover_cobranca.assert_called_once_with("c1")

    def _com_pix(self, status="pendente"):
        self.mensalidade.woovi_correlation_id = "c1"
        self.mensalidade.status = status
        self.mensalidade.save(update_fields=["woovi_correlation_id", "status"])

    def test_confirmar_da_baixa_quando_a_woovi_diz_completed(self, mock_client_class):
        mock_client_class.return_value.obter_cobranca.return_value = {
            "status": "COMPLETED", "paidAt": "2026-09-08T15:07:50.891Z",
        }
        self._com_pix()

        self.assertIsNotNone(confirmar_pagamento_pix("c1"))

        self.mensalidade.refresh_from_db()
        self.assertEqual(self.mensalidade.status, "paga")
        self.assertEqual(self.mensalidade.forma_pagamento, "pix")
        self.assertEqual(self.mensalidade.pago_em.date(), date(2026, 9, 8))

    def test_confirmar_nao_da_baixa_se_a_woovi_nao_confirmar(self, mock_client_class):
        mock_client_class.return_value.obter_cobranca.return_value = {"status": "ACTIVE"}
        self._com_pix()

        self.assertIsNone(confirmar_pagamento_pix("c1"))

        self.mensalidade.refresh_from_db()
        self.assertEqual(self.mensalidade.status, "pendente")

    def test_confirmar_correlation_id_desconhecido_nao_chama_a_woovi(self, mock_client_class):
        self.assertIsNone(confirmar_pagamento_pix("inexistente"))
        mock_client_class.assert_not_called()

    def test_confirmar_mensalidade_ja_paga_nao_chama_a_woovi(self, mock_client_class):
        self._com_pix(status="paga")
        self.assertIsNone(confirmar_pagamento_pix("c1"))
        mock_client_class.assert_not_called()

    def test_pix_pago_de_mensalidade_cancelada_registra_aviso_sem_baixa(self, mock_client_class):
        mock_client_class.return_value.obter_cobranca.return_value = {"status": "COMPLETED"}
        self._com_pix(status="cancelada")

        with self.assertLogs("integracoes.woovi.services", level="WARNING") as logs:
            self.assertIsNone(confirmar_pagamento_pix("c1"))

        self.assertIn("c1", logs.output[0])
        self.mensalidade.refresh_from_db()
        self.assertEqual(self.mensalidade.status, "cancelada")
