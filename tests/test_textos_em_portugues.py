"""Textos da interface em português (o Django 6.1 trouxe "- Select an option -"
sem tradução para pt-BR)."""
from django.contrib.auth import get_user_model
from django.test import TestCase

from academias.models import Academia
from portal.models import AcessoAcademia


class TextosEmPortuguesTests(TestCase):
    def setUp(self):
        academia = Academia.objects.create(nome="Keiko", cnpj="PT1")
        usuario = get_user_model().objects.create_user("dona", password="x")
        AcessoAcademia.objects.create(usuario=usuario, academia=academia, administrador=True)
        self.client.force_login(usuario)

    def test_opcao_vazia_dos_selects(self):
        resp = self.client.get("/matriculas/convites/")
        self.assertContains(resp, "- Selecione uma opção -")
        self.assertNotContains(resp, "Select an option")

    def test_caminho_no_topo_da_pagina(self):
        resp = self.client.get("/painel/")
        self.assertContains(resp, "Painel <span>/</span>")
        self.assertNotContains(resp, "Workspace")
