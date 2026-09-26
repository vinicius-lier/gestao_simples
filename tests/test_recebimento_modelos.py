from datetime import date, timedelta
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.test import TestCase
from django.utils import timezone

from academias.models import Academia
from atletas.models import Atleta
from financeiro.models import CobrancaPix, ContaRecebimento, Mensalidade, Repasse
from matriculas.models import Matricula
from modalidades.models import Modalidade


def criar_mensalidade(academia, competencia=date(2026, 9, 1)):
    atleta = Atleta.objects.create(academia=academia, nome="Ana")
    matricula = Matricula.objects.create(
        academia=academia, atleta=atleta, modalidade=Modalidade.objects.create(academia=academia, nome="Judô"),
        valor_mensalidade=Decimal("120.00"), dia_vencimento=10, data_inicio=date(2026, 1, 1),
    )
    return Mensalidade.objects.create(
        academia=academia, matricula=matricula, competencia=competencia,
        valor=Decimal("120.00"), vencimento=competencia.replace(day=10),
    )


class GarantiasDoBancoTests(TestCase):
    def setUp(self):
        self.academia = Academia.objects.create(nome="Escola", cnpj="RM1")
        self.conta = ContaRecebimento.objects.create(
            academia=self.academia, tipo_chave=ContaRecebimento.EMAIL, pix_key="escola@exemplo.com",
        )

    def test_uma_unica_conta_ativa_por_academia(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            ContaRecebimento.objects.create(
                academia=self.academia, tipo_chave=ContaRecebimento.CPF, pix_key="52998224725",
            )

    def test_contas_inativas_ficam_como_historico(self):
        self.conta.ativa = False
        self.conta.save()
        for chave in ("a@exemplo.com", "b@exemplo.com"):
            ContaRecebimento.objects.create(
                academia=self.academia, tipo_chave=ContaRecebimento.EMAIL, pix_key=chave, ativa=False,
            )
        ContaRecebimento.objects.create(academia=self.academia, tipo_chave=ContaRecebimento.EMAIL, pix_key="c@exemplo.com")
        self.assertEqual(self.academia.contas_recebimento.count(), 4)
        self.assertEqual(ContaRecebimento.ativa_da(self.academia).pix_key, "c@exemplo.com")

    def test_um_unico_pix_ativo_por_mensalidade(self):
        mensalidade = criar_mensalidade(self.academia)
        dados = dict(
            mensalidade=mensalidade, conta_recebimento=self.conta, valor=Decimal("120"),
            br_code="000201", expira_em=timezone.now() + timedelta(days=1),
        )
        CobrancaPix.objects.create(correlation_id="c1", **dados)
        with self.assertRaises(IntegrityError), transaction.atomic():
            CobrancaPix.objects.create(correlation_id="c2", **dados)
        CobrancaPix.objects.create(correlation_id="c3", **{**dados, "status": CobrancaPix.EXPIRADA})

    def test_um_unico_repasse_aberto_por_conta(self):
        Repasse.objects.create(academia=self.academia, conta_recebimento=self.conta, pix_key_destino=self.conta.pix_key)
        for status in (Repasse.PENDENTE, Repasse.PROCESSANDO, Repasse.FALHA):
            with self.subTest(status=status), self.assertRaises(IntegrityError), transaction.atomic():
                Repasse.objects.create(
                    academia=self.academia, conta_recebimento=self.conta,
                    pix_key_destino=self.conta.pix_key, status=status,
                )
        for status in (Repasse.CONCLUIDA, Repasse.REQUER_ATENCAO):
            Repasse.objects.create(
                academia=self.academia, conta_recebimento=self.conta,
                pix_key_destino=self.conta.pix_key, status=status,
            )

    def test_historico_financeiro_nao_pode_ser_apagado(self):
        mensalidade = criar_mensalidade(self.academia)
        CobrancaPix.objects.create(
            mensalidade=mensalidade, conta_recebimento=self.conta, correlation_id="c1", valor=Decimal("120"),
            br_code="000201", expira_em=timezone.now(),
        )
        with self.assertRaises(ProtectedError):
            self.conta.delete()
        with self.assertRaises(ProtectedError):
            mensalidade.delete()

    def test_pix_vigente_usa_cobranca_ativa_nao_expirada(self):
        mensalidade = criar_mensalidade(self.academia)
        self.assertFalse(mensalidade.pix_vigente)
        cobranca = CobrancaPix.objects.create(
            mensalidade=mensalidade, conta_recebimento=self.conta, correlation_id="c1", valor=Decimal("120"),
            br_code="000201", expira_em=timezone.now() - timedelta(minutes=1),
        )
        self.assertFalse(mensalidade.pix_vigente)
        cobranca.expira_em = timezone.now() + timedelta(days=1)
        cobranca.save()
        self.assertEqual(mensalidade.cobranca_pix_vigente, cobranca)
