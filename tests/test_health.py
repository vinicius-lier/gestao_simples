"""Health check usado pelo HEALTHCHECK do Dockerfile e pelo Coolify."""
from unittest.mock import patch

from django.db import OperationalError
from django.test import TestCase


class HealthTests(TestCase):
    def test_responde_ok_sem_login(self):
        resp = self.client.get("/health/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"status": "ok", "database": "ok"})

    def test_head_tambem_responde(self):
        self.assertEqual(self.client.head("/health/").status_code, 200)

    def test_banco_fora_do_ar_responde_503(self):
        with patch("config.views.connection.cursor", side_effect=OperationalError("sem conexão")):
            with self.assertLogs("config.views", level="ERROR"):
                resp = self.client.get("/health/")
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.json(), {"status": "erro", "database": "indisponivel"})

    def test_so_aceita_leitura(self):
        self.assertEqual(self.client.post("/health/").status_code, 405)
