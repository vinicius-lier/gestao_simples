"""FASE 2A — painel Configurações → WhatsApp (login, admin, tenant, QR)."""
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from academias.models import Academia, IntegracaoWhatsApp
from portal.models import AcessoAcademia

ACOES = [
    "/configuracoes/whatsapp/criar/",
    "/configuracoes/whatsapp/qrcode/",
    "/configuracoes/whatsapp/status/",
    "/configuracoes/whatsapp/desconectar/",
    "/configuracoes/whatsapp/numero/",
]


class WhatsAppConfigViewsTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.a = Academia.objects.create(nome="Academia A", cnpj="WCV-A")
        self.b = Academia.objects.create(nome="Academia B", cnpj="WCV-B")

        self.admin_a = User.objects.create_user("admin_a", password="x")
        AcessoAcademia.objects.create(usuario=self.admin_a, academia=self.a, administrador=True)

        self.membro_a = User.objects.create_user("membro_a", password="x")
        AcessoAcademia.objects.create(usuario=self.membro_a, academia=self.a, administrador=False)

        self.config_b = IntegracaoWhatsApp.objects.create(
            academia=self.b,
            provider=IntegracaoWhatsApp.PROVIDER_EVOLUTION,
            evolution_instance_name="b-instance",
            status_conexao=IntegracaoWhatsApp.STATUS_CONECTADO,
        )

    # -------------------------------------------------------------- acesso
    def test_config_exige_login(self):
        resp = self.client.get("/configuracoes/whatsapp/")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login/", resp["Location"])

    def test_config_get_para_membro(self):
        self.client.force_login(self.membro_a)
        resp = self.client.get("/configuracoes/whatsapp/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "WhatsApp")

    def test_salvar_config_exige_admin(self):
        self.client.force_login(self.membro_a)
        resp = self.client.post(
            "/configuracoes/whatsapp/",
            {"provider": "evolution", "evolution_instance_name": "hack"},
        )
        self.assertEqual(resp.status_code, 403)

    def test_admin_salva_o_whatsapp_de_avisos_so_com_numeros(self):
        self.client.force_login(self.admin_a)
        resp = self.client.post(
            "/configuracoes/whatsapp/", {"provider": "evolution", "numero_avisos": "(21) 97777-6666"},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(IntegracaoWhatsApp.objects.get(academia=self.a).numero_avisos, "21977776666")

    def test_whatsapp_de_avisos_sem_ddd_e_recusado(self):
        self.client.force_login(self.admin_a)
        self.client.post("/configuracoes/whatsapp/", {"provider": "evolution", "numero_avisos": "97777"})
        self.assertEqual(IntegracaoWhatsApp.objects.get(academia=self.a).numero_avisos, "")

    def test_admin_salva_config(self):
        self.client.force_login(self.admin_a)
        resp = self.client.post(
            "/configuracoes/whatsapp/",
            {
                "provider": "evolution",
                "evolution_base_url": "https://evo.example.com",
                "evolution_instance_name": "academia-a",
                "credencial_ref": "EVOLUTION_API_KEY_A",
                "n8n_webhook_url": "",
            },
        )
        self.assertEqual(resp.status_code, 302)
        cfg = IntegracaoWhatsApp.objects.get(academia=self.a)
        self.assertEqual(cfg.provider, "evolution")
        self.assertEqual(cfg.evolution_instance_name, "academia-a")
        self.assertEqual(cfg.credencial_ref, "EVOLUTION_API_KEY_A")

    def test_acoes_exigem_post(self):
        self.client.force_login(self.admin_a)
        for url in ACOES:
            self.assertEqual(self.client.get(url).status_code, 405, url)

    def test_acoes_exigem_admin(self):
        self.client.force_login(self.membro_a)
        for url in ACOES:
            self.assertEqual(self.client.post(url).status_code, 403, url)

    # ----------------------------------------------------- isolamento tenant
    def test_admin_de_a_so_controla_a_instancia_de_a(self):
        IntegracaoWhatsApp.objects.create(
            academia=self.a,
            provider=IntegracaoWhatsApp.PROVIDER_EVOLUTION,
            evolution_instance_name="academia-a",
            credencial_ref="EVOLUTION_API_KEY_A",
            status_conexao=IntegracaoWhatsApp.STATUS_CONECTADO,
        )
        self.client.force_login(self.admin_a)
        with patch("integracoes.evolution.services.client_para_config") as factory:
            cliente = Mock(**{
                "buscar_estado_conexao.return_value": {"instance": {"state": "close"}}
            })
            factory.return_value = cliente
            self.client.post("/configuracoes/whatsapp/status/")
            # a config passada ao factory é sempre a da academia do usuário
            self.assertEqual(
                factory.call_args.args[0].evolution_instance_name, "academia-a"
            )

        self.config_b.refresh_from_db()
        self.assertEqual(self.config_b.status_conexao, IntegracaoWhatsApp.STATUS_CONECTADO)
        cfg_a = IntegracaoWhatsApp.objects.get(academia=self.a)
        self.assertEqual(cfg_a.status_conexao, IntegracaoWhatsApp.STATUS_DESCONECTADO)

    # ------------------------------------------------------------------ QR
    def test_qrcode_renderiza_mas_nao_persiste(self):
        IntegracaoWhatsApp.objects.create(
            academia=self.a,
            provider=IntegracaoWhatsApp.PROVIDER_EVOLUTION,
            evolution_instance_name="academia-a",
            credencial_ref="EVOLUTION_API_KEY_A",
        )
        self.client.force_login(self.admin_a)
        with patch("integracoes.evolution.services.client_para_config") as factory:
            factory.return_value = Mock(**{
                "obter_qrcode.return_value": {"base64": "ZM9v", "code": "1@pair"}
            })
            resp = self.client.post("/configuracoes/whatsapp/qrcode/")

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "data:image/png;base64,ZM9v")
        cfg = IntegracaoWhatsApp.objects.get(academia=self.a)
        self.assertEqual(cfg.status_conexao, IntegracaoWhatsApp.STATUS_AGUARDANDO_QRCODE)
        for campo in ("ultimo_status", "numero_whatsapp"):
            self.assertNotIn("ZM9v", getattr(cfg, campo))

    def test_status_atualiza_e_redireciona(self):
        IntegracaoWhatsApp.objects.create(
            academia=self.a,
            provider=IntegracaoWhatsApp.PROVIDER_EVOLUTION,
            evolution_instance_name="academia-a",
            credencial_ref="EVOLUTION_API_KEY_A",
        )
        self.client.force_login(self.admin_a)
        with patch("integracoes.evolution.services.client_para_config") as factory:
            factory.return_value = Mock(**{
                "buscar_estado_conexao.return_value": {
                    "instance": {"state": "open", "owner": "5521999998888@s.whatsapp.net"}
                }
            })
            resp = self.client.post("/configuracoes/whatsapp/status/")

        self.assertEqual(resp.status_code, 302)
        cfg = IntegracaoWhatsApp.objects.get(academia=self.a)
        self.assertEqual(cfg.status_conexao, IntegracaoWhatsApp.STATUS_CONECTADO)
        self.assertEqual(cfg.numero_whatsapp, "5521999998888")

    def test_desconectar_atualiza_estado(self):
        IntegracaoWhatsApp.objects.create(
            academia=self.a,
            provider=IntegracaoWhatsApp.PROVIDER_EVOLUTION,
            evolution_instance_name="academia-a",
            credencial_ref="EVOLUTION_API_KEY_A",
            status_conexao=IntegracaoWhatsApp.STATUS_CONECTADO,
        )
        self.client.force_login(self.admin_a)
        with patch("integracoes.evolution.services.client_para_config") as factory:
            factory.return_value = Mock(**{"logout.return_value": {}})
            self.client.post("/configuracoes/whatsapp/desconectar/")

        cfg = IntegracaoWhatsApp.objects.get(academia=self.a)
        self.assertEqual(cfg.status_conexao, IntegracaoWhatsApp.STATUS_DESCONECTADO)

    def test_trocar_numero(self):
        IntegracaoWhatsApp.objects.create(
            academia=self.a,
            provider=IntegracaoWhatsApp.PROVIDER_EVOLUTION,
            evolution_instance_name="academia-a",
            credencial_ref="EVOLUTION_API_KEY_A",
        )
        self.client.force_login(self.admin_a)
        with patch("integracoes.evolution.services.client_para_config") as factory:
            factory.return_value = Mock(**{"logout.return_value": {}})
            self.client.post(
                "/configuracoes/whatsapp/numero/", {"numero_whatsapp": "21 98888-7777"}
            )

        cfg = IntegracaoWhatsApp.objects.get(academia=self.a)
        self.assertEqual(cfg.numero_whatsapp, "5521988887777")
        self.assertEqual(cfg.status_conexao, IntegracaoWhatsApp.STATUS_DESCONECTADO)
