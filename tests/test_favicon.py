"""Logo da Escola de Judô Keiko Fukuda como ícone da aba."""
from django.test import TestCase
from django.urls import reverse

LOGO = "/static/portal/logo-fukuda.png"


class FaviconTests(TestCase):
    def test_favicon_ico_redireciona_para_o_logo(self):
        resp = self.client.get("/favicon.ico")
        self.assertEqual(resp.status_code, 301)
        self.assertEqual(resp["Location"], LOGO)

    def test_todos_os_layouts_declaram_o_logo_como_icone(self):
        paginas = {
            "portal (login)": reverse("portal:login"),
            "página pública": reverse("portal:publica"),
            "layout público": reverse("portal:experimentais_publico"),
        }
        for nome, url in paginas.items():
            with self.subTest(nome):
                self.assertRegex(
                    self.client.get(url).content.decode(),
                    rf'<link rel="icon"[^>]*href="{LOGO}"',
                )
