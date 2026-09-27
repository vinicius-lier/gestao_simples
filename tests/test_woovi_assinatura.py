import base64
from unittest.mock import patch

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from django.core.cache import cache
from django.test import SimpleTestCase

from integracoes.woovi import assinatura
from integracoes.woovi.exceptions import WooviUnavailableError


def gerar_chave():
    privada = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = privada.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()
    return privada, pem


def assinar(privada, corpo):
    return base64.b64encode(privada.sign(corpo, padding.PKCS1v15(), hashes.SHA256())).decode()


CORPO = b'{"event":"OPENPIX:CHARGE_COMPLETED","charge":{"correlationID":"c1"}}'


@patch("integracoes.woovi.assinatura.WooviClient")
class AssinaturaWebhookTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.privada, self.pem = gerar_chave()

    def tearDown(self):
        cache.clear()

    def test_assinatura_valida(self, mock_client):
        mock_client.return_value.chaves_publicas_webhook.return_value = [self.pem]
        self.assertTrue(assinatura.assinatura_valida(CORPO, assinar(self.privada, CORPO)))

    def test_corpo_alterado_invalida_a_assinatura(self, mock_client):
        mock_client.return_value.chaves_publicas_webhook.return_value = [self.pem]
        sig = assinar(self.privada, CORPO)
        self.assertFalse(assinatura.assinatura_valida(CORPO.replace(b"c1", b"c2"), sig))

    def test_sem_assinatura_ou_base64_invalido(self, mock_client):
        self.assertFalse(assinatura.assinatura_valida(CORPO, ""))
        self.assertFalse(assinatura.assinatura_valida(CORPO, "@@@não-é-base64"))
        mock_client.assert_not_called()

    def test_chaves_ficam_em_cache(self, mock_client):
        mock_client.return_value.chaves_publicas_webhook.return_value = [self.pem]
        sig = assinar(self.privada, CORPO)

        assinatura.assinatura_valida(CORPO, sig)
        assinatura.assinatura_valida(CORPO, sig)

        mock_client.return_value.chaves_publicas_webhook.assert_called_once()

    def test_rotacao_busca_a_lista_de_novo_e_aceita_a_chave_nova(self, mock_client):
        _antiga, pem_antiga = gerar_chave()
        cache.set(assinatura.CHAVE_CACHE, [pem_antiga])
        mock_client.return_value.chaves_publicas_webhook.return_value = [pem_antiga, self.pem]

        self.assertTrue(assinatura.assinatura_valida(CORPO, assinar(self.privada, CORPO)))
        mock_client.return_value.chaves_publicas_webhook.assert_called_once()

    def test_assinatura_forjada_nao_faz_o_servidor_martelar_a_woovi(self, mock_client):
        mock_client.return_value.chaves_publicas_webhook.return_value = [self.pem]
        forjadora, _ = gerar_chave()

        for _ in range(5):
            self.assertFalse(assinatura.assinatura_valida(CORPO, assinar(forjadora, CORPO)))

        # 1 busca inicial + no máximo 1 atualização no intervalo mínimo.
        self.assertLessEqual(mock_client.return_value.chaves_publicas_webhook.call_count, 2)

    def test_sem_acesso_a_woovi_usa_a_chave_documentada(self, mock_client):
        mock_client.return_value.chaves_publicas_webhook.side_effect = WooviUnavailableError("fora")
        self.assertEqual(assinatura._chaves(), [assinatura.CHAVE_DOCUMENTADA])
        # A chave documentada é uma chave RSA válida.
        serialization.load_pem_public_key(assinatura.CHAVE_DOCUMENTADA.encode())
