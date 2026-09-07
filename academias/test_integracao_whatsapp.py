"""FASE 1C — model de configuração da integração de WhatsApp por academia."""
from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase

from academias.admin import IntegracaoWhatsAppAdmin
from academias.models import Academia, IntegracaoWhatsApp
from portal.models import AcessoAcademia


class IntegracaoWhatsAppModelTests(TestCase):
    def setUp(self):
        self.academia = Academia.objects.create(nome="Ac", cnpj="IW1")

    def test_defaults_seguros(self):
        cfg = IntegracaoWhatsApp.objects.create(academia=self.academia)
        self.assertEqual(cfg.provider, IntegracaoWhatsApp.PROVIDER_META)
        self.assertEqual(cfg.status_conexao, IntegracaoWhatsApp.STATUS_DESCONECTADO)
        self.assertEqual(cfg.numero_whatsapp, "")
        self.assertEqual(cfg.n8n_webhook_url, "")
        self.assertEqual(cfg.credencial_ref, "")
        self.assertIsNone(cfg.ultima_conexao_em)

    def test_uma_config_por_academia(self):
        IntegracaoWhatsApp.objects.create(academia=self.academia)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                IntegracaoWhatsApp.objects.create(academia=self.academia)

    def test_nao_guarda_credencial_apenas_referencia(self):
        campos = {f.name for f in IntegracaoWhatsApp._meta.get_fields()}
        # nada de campo para segredo em texto claro
        for proibido in ("api_key", "token", "secret", "evolution_api_key", "n8n_token"):
            self.assertNotIn(proibido, campos)
        self.assertIn("credencial_ref", campos)

    def test_str(self):
        cfg = IntegracaoWhatsApp.objects.create(
            academia=self.academia, provider=IntegracaoWhatsApp.PROVIDER_EVOLUTION
        )
        self.assertIn("Evolution", str(cfg))


class IntegracaoWhatsAppAdminIsolamentoTests(TestCase):
    def setUp(self):
        self.a = Academia.objects.create(nome="A", cnpj="AWA")
        self.b = Academia.objects.create(nome="B", cnpj="AWB")
        self.cfg_a = IntegracaoWhatsApp.objects.create(academia=self.a)
        self.cfg_b = IntegracaoWhatsApp.objects.create(academia=self.b)
        self.admin = IntegracaoWhatsAppAdmin(IntegracaoWhatsApp, AdminSite())
        User = get_user_model()
        self.gestor = User.objects.create_user("gestor", password="x")
        AcessoAcademia.objects.create(usuario=self.gestor, academia=self.a)
        self.root = User.objects.create_superuser("root", "r@x.com", "x")

    def _request(self, user):
        class _R:
            pass

        r = _R()
        r.user = user
        return r

    def test_gestor_so_ve_sua_academia(self):
        qs = self.admin.get_queryset(self._request(self.gestor))
        self.assertCountEqual(list(qs), [self.cfg_a])

    def test_superusuario_ve_tudo(self):
        qs = self.admin.get_queryset(self._request(self.root))
        self.assertCountEqual(list(qs), [self.cfg_a, self.cfg_b])

    def test_secrets_nao_estao_no_list_display(self):
        for campo in self.admin.list_display:
            self.assertNotIn("token", campo)
            self.assertNotIn("key", campo)
