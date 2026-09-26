from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from financeiro.models import CobrancaPix, Repasse
from tests.woovi_base import CHAVE, CenarioWoovi


class AdminFinanceiroTests(CenarioWoovi, TestCase):
    """O /admin/ é da plataforma: listas e buscas dos modelos financeiros
    precisam abrir (search_fields não é validado pelo `check`)."""

    def setUp(self):
        self.criar_cenario()
        self.cobranca(correlation_id="mensalidade-busca")
        self.repasse = Repasse.objects.create(
            academia=self.academia, conta_recebimento=self.conta, pix_key_destino=CHAVE,
            status=Repasse.REQUER_ATENCAO, tentativas=6,
        )
        self.client.force_login(get_user_model().objects.create_superuser("plataforma", password="senha123"))

    def test_listas_e_buscas_abrem(self):
        for url in (
            "/admin/financeiro/mensalidade/?q=mensalidade-busca",
            "/admin/financeiro/cobrancapix/?q=mensalidade-busca",
            "/admin/financeiro/contarecebimento/?q=keiko",
            "/admin/financeiro/repasse/?status__exact=requer_atencao",
            "/admin/financeiro/eventowebhook/",
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)
        self.assertContains(self.client.get("/admin/financeiro/mensalidade/?q=mensalidade-busca"), "Ana")

    def test_acao_tentar_de_novo_reabre_o_repasse(self):
        self.client.post("/admin/financeiro/repasse/", {
            "action": "tentar_de_novo", "_selected_action": [self.repasse.pk],
        })
        self.repasse.refresh_from_db()
        self.assertEqual((self.repasse.status, self.repasse.tentativas), (Repasse.PENDENTE, 0))
        self.assertLessEqual(self.repasse.proxima_tentativa_em, timezone.now() + timedelta(seconds=1))

    def test_historico_financeiro_nao_pode_ser_apagado_pelo_admin(self):
        cobranca = CobrancaPix.objects.get()
        self.assertEqual(self.client.get(f"/admin/financeiro/cobrancapix/{cobranca.pk}/delete/").status_code, 403)
        self.assertEqual(self.client.get(f"/admin/financeiro/repasse/{self.repasse.pk}/delete/").status_code, 403)
