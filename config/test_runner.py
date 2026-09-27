"""Executor de testes do projeto.

Nenhum teste pode chegar à API real da Woovi, mesmo com um WOOVI_APP_ID de
verdade no .env: a base aponta para um host reservado (.invalid) e qualquer
requisição HTTP do cliente Woovi que o teste não tenha simulado falha na
hora, em vez de sair para a rede. O bloqueio vale só para o módulo do
cliente Woovi — os demais clientes (Evolution, Meta, n8n) não são afetados.
"""
from unittest import mock

import requests
from django.conf import settings
from django.test.runner import DiscoverRunner


class ChamadaRealProibida(AssertionError):
    """Um teste tentou falar com a API real da Woovi."""


class _RequestsSemRede:
    """Substitui o ``requests`` visto por integracoes.woovi.client: as
    exceções e demais atributos continuam os de verdade, mas ``request``
    falha. Um teste que simula ``integracoes.woovi.client.requests.request``
    troca este método normalmente."""

    def __getattr__(self, nome):
        return getattr(requests, nome)

    @staticmethod
    def request(*args, **kwargs):
        raise ChamadaRealProibida(
            "Teste tentou chamar a API real da Woovi. Simule o WooviClient ou "
            "integracoes.woovi.client.requests.request."
        )


class ProjetoTestRunner(DiscoverRunner):
    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)
        settings.WOOVI_BASE_URL = "https://woovi.invalid"
        settings.WOOVI_APP_ID = "app-id-somente-para-testes"
        self._bloqueio_woovi = mock.patch("integracoes.woovi.client.requests", _RequestsSemRede())
        self._bloqueio_woovi.start()

    def teardown_test_environment(self, **kwargs):
        self._bloqueio_woovi.stop()
        super().teardown_test_environment(**kwargs)
