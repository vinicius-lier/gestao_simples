import logging
from unittest.mock import Mock, patch

import requests
from django.test import SimpleTestCase, override_settings

from integracoes.woovi.client import WooviClient
from integracoes.woovi.exceptions import (
    WooviAuthError,
    WooviConfigError,
    WooviInvalidResponseError,
    WooviNotFoundError,
    WooviRequestError,
    WooviTimeoutError,
    WooviUnavailableError,
)

APP_ID = "Q2xpZW50X0lkXzEyMzpzZWdyZWRvLW11aXRvLXNlY3JldG8="
CONFIG = {"WOOVI_BASE_URL": "https://api.woovi.com/", "WOOVI_APP_ID": APP_ID}
BASE = "https://api.woovi.com"


def resposta(status=200, json=None):
    r = Mock(status_code=status)
    if isinstance(json, Exception):
        r.json.side_effect = json
    else:
        r.json.return_value = json if json is not None else {}
    return r


@override_settings(**CONFIG)
@patch("integracoes.woovi.client.requests.request")
class WooviClientTests(SimpleTestCase):
    def setUp(self):
        self.client = WooviClient()

    # ------------------------------------------------------------ transporte
    def test_autentica_com_o_app_id_cru_sem_bearer(self, mock_request):
        mock_request.return_value = resposta(json={"charge": {"correlationID": "c1"}})
        self.client.obter_cobranca("c1")
        headers = mock_request.call_args.kwargs["headers"]
        self.assertEqual(headers["Authorization"], APP_ID)
        self.assertEqual(mock_request.call_args.kwargs["timeout"], 30)

    def test_app_id_nao_aparece_no_repr(self, mock_request):
        self.assertNotIn(APP_ID, repr(self.client))
        self.assertNotIn(APP_ID, str(vars(self.client).get("base_url")))

    @override_settings(WOOVI_APP_ID="")
    def test_sem_app_id_levanta_erro_de_configuracao(self, mock_request):
        with self.assertRaisesMessage(WooviConfigError, "WOOVI_APP_ID"):
            WooviClient()
        mock_request.assert_not_called()

    def test_codigos_http_viram_excecoes_especificas(self, mock_request):
        casos = [
            (400, {"error": "Valor inválido"}, WooviRequestError),
            (400, {"error": "Subaccount not found: x@y.com"}, WooviNotFoundError),
            (401, {"error": "Invalid Authorization header"}, WooviAuthError),
            (403, {"error": "This feature is not enabled for your company"}, WooviAuthError),
            (404, {}, WooviNotFoundError),
            (500, ValueError("sem json"), WooviUnavailableError),
        ]
        for status, corpo, excecao in casos:
            with self.subTest(status=status, corpo=corpo):
                mock_request.return_value = resposta(status, corpo)
                with self.assertRaises(excecao) as ctx:
                    self.client.obter_subconta("x@y.com")
                self.assertEqual(ctx.exception.status_code, status)
                self.assertNotIn(APP_ID, str(ctx.exception))

    def test_mensagem_de_erro_traz_o_texto_da_woovi(self, mock_request):
        mock_request.return_value = resposta(400, {"error": "Cobrança já foi paga"})
        with self.assertRaisesMessage(WooviRequestError, "HTTP 400: Cobrança já foi paga"):
            self.client.remover_cobranca("c1")

    def test_timeout_vira_erro_de_resultado_desconhecido(self, mock_request):
        mock_request.side_effect = requests.Timeout(f"lento {APP_ID}")
        with self.assertRaises(WooviTimeoutError) as ctx:
            self.client.sacar_subconta("x@y.com", 100)
        self.assertIsInstance(ctx.exception, WooviUnavailableError)
        self.assertNotIn(APP_ID, str(ctx.exception))
        self.assertIsNone(ctx.exception.__cause__)

    def test_erro_de_conexao_nao_expoe_credenciais(self, mock_request):
        mock_request.side_effect = requests.ConnectionError(f"falhou com {APP_ID}")
        with self.assertRaises(WooviUnavailableError) as ctx:
            self.client.obter_cobranca("c1")
        self.assertNotIn(APP_ID, str(ctx.exception))
        self.assertIsNone(ctx.exception.__cause__)

    def test_resposta_que_nao_e_json_e_erro(self, mock_request):
        mock_request.return_value = resposta(200, ValueError("html"))
        with self.assertRaises(WooviInvalidResponseError):
            self.client.obter_cobranca("c1")

    def test_segredo_nunca_vai_para_os_logs(self, mock_request):
        mock_request.side_effect = requests.ConnectionError(APP_ID)
        with self.assertLogs(level=logging.DEBUG) as logs:
            logging.getLogger("teste").debug("marcador")
            try:
                self.client.obter_subconta("x@y.com")
            except WooviUnavailableError as exc:
                logging.getLogger("integracoes.woovi").error("falhou: %s", exc)
        self.assertNotIn(APP_ID, "\n".join(logs.output))

    # ------------------------------------------------------------- subcontas
    def test_listar_subcontas_aceita_as_duas_grafias_documentadas(self, mock_request):
        for chave in ("subaccounts", "subAccounts"):
            with self.subTest(chave=chave):
                mock_request.return_value = resposta(json={chave: [
                    {"name": "Escola", "pixKey": "a@b.com", "balance": 700, "withdrawBlocked": False},
                ]})
                subcontas = self.client.listar_subcontas()
                self.assertEqual(mock_request.call_args.args, ("GET", f"{BASE}/api/v1/subaccount"))
                self.assertEqual([(s.pix_key, s.saldo_centavos) for s in subcontas], [("a@b.com", 700)])

        mock_request.return_value = resposta(json={"outra": []})
        with self.assertRaises(WooviInvalidResponseError):
            self.client.listar_subcontas()

    def test_criar_subconta(self, mock_request):
        mock_request.return_value = resposta(json={"SubAccount": {"name": "Escola", "pixKey": "a@b.com"}})

        subconta = self.client.criar_ou_obter_subconta("a@b.com", "Escola")

        args, kwargs = mock_request.call_args
        self.assertEqual(args, ("POST", f"{BASE}/api/v1/subaccount"))
        self.assertEqual(kwargs["json"], {"pixKey": "a@b.com", "name": "Escola"})
        self.assertEqual(subconta.pix_key, "a@b.com")
        self.assertEqual(subconta.saldo_centavos, 0)
        self.assertFalse(subconta.saque_bloqueado)

    def test_criar_subconta_que_ja_existe_devolve_a_existente_com_saldo(self, mock_request):
        mock_request.return_value = resposta(json={
            "SubAccount": {"name": "Escola", "pixKey": "a@b.com", "balance": 15000, "withdrawBlocked": False},
        })
        self.assertEqual(self.client.criar_ou_obter_subconta("a@b.com", "Escola").saldo_centavos, 15000)

    def test_consultar_saldo_codifica_a_chave_na_url(self, mock_request):
        mock_request.return_value = resposta(json={
            "SubAccount": {"pixKey": "+5521999998888", "balance": 250, "withdrawBlocked": True},
        })

        subconta = self.client.obter_subconta("+5521999998888")

        self.assertEqual(mock_request.call_args.args, ("GET", f"{BASE}/api/v1/subaccount/%2B5521999998888"))
        self.assertEqual(subconta.saldo_centavos, 250)
        self.assertTrue(subconta.saque_bloqueado)

    def test_resposta_de_subconta_sem_chave_e_invalida(self, mock_request):
        mock_request.return_value = resposta(json={"SubAccount": {"name": "x"}})
        with self.assertRaises(WooviInvalidResponseError):
            self.client.obter_subconta("a@b.com")

    def test_saque_envia_o_valor_e_aceita_o_formato_transaction(self, mock_request):
        mock_request.return_value = resposta(json={"transaction": {
            "status": "CREATED", "value": 15000, "endToEndId": "E123",
            "correlationID": "saque-1", "destinationAlias": "a@b.com",
        }})

        saque = self.client.sacar_subconta("a@b.com", 15000)

        args, kwargs = mock_request.call_args
        self.assertEqual(args, ("POST", f"{BASE}/api/v1/subaccount/a%40b.com/withdraw"))
        self.assertEqual(kwargs["json"], {"value": 15000})
        self.assertEqual(saque.status, "CREATED")
        self.assertEqual(saque.valor_centavos, 15000)
        self.assertEqual(saque.correlation_id, "saque-1")
        self.assertEqual(saque.end_to_end_id, "E123")

    def test_saque_aceita_o_formato_withdraw_account_do_schema(self, mock_request):
        mock_request.return_value = resposta(json={"withdraw": {"account": {
            "status": "CREATED", "value": 900, "correlationID": "saque-2", "destinationAlias": "a@b.com",
        }}})

        saque = self.client.sacar_subconta("a@b.com", 900)

        self.assertEqual((saque.correlation_id, saque.valor_centavos, saque.end_to_end_id), ("saque-2", 900, ""))

    def test_creditar_subconta(self, mock_request):
        mock_request.return_value = resposta(json={"pixKey": "a@b.com", "value": 115, "success": "ok"})

        self.client.creditar_subconta("+5521999998888", 115, descricao="Pix c1")

        args, kwargs = mock_request.call_args
        self.assertEqual(args, ("POST", f"{BASE}/api/v1/subaccount/%2B5521999998888/credit"))
        self.assertEqual(kwargs["json"], {"value": 115, "description": "Pix c1"})

    def test_cobranca_le_a_taxa_quando_vem(self, mock_request):
        mock_request.return_value = resposta(json={"charge": {"correlationID": "c1", "status": "COMPLETED", "value": 200, "fee": 85}})
        self.assertEqual(self.client.obter_cobranca("c1").taxa_centavos, 85)
        mock_request.return_value = resposta(json={"charge": {"correlationID": "c1", "status": "COMPLETED", "value": 200}})
        self.assertIsNone(self.client.obter_cobranca("c1").taxa_centavos)

    def test_extrato(self, mock_request):
        mock_request.return_value = resposta(json=[
            {"id": "1", "time": "2026-09-26T12:00:00.000Z", "value": 1000, "balance": 0, "type": "DEBIT", "operationType": "WITHDRAWAL"},
            {"id": "2", "time": "2026-09-26T11:00:00.000Z", "value": 1000, "balance": 1000, "type": "CREDIT", "operationType": None},
        ])

        extrato = self.client.extrato_subconta("a@b.com")

        self.assertEqual([l.operacao for l in extrato], ["WITHDRAWAL", "CREDIT"])
        self.assertEqual(extrato[0].valor_centavos, 1000)
        self.assertEqual(extrato[0].momento.year, 2026)

    # ------------------------------------------------------------- cobranças
    def test_criar_cobranca_idempotente_com_split_para_subconta(self, mock_request):
        mock_request.return_value = resposta(json={"charge": {
            "correlationID": "c1", "status": "ACTIVE", "value": 12000, "brCode": "000201",
            "paymentLinkUrl": "https://woovi.com/pay/x", "expiresDate": "2026-10-26T12:00:00.000Z",
        }})
        splits = [{"value": 12000, "pixKey": "a@b.com", "splitType": "SPLIT_SUB_ACCOUNT"}]

        cobranca = self.client.criar_cobranca(
            correlation_id="c1", valor_centavos=12000, comentario="Mensalidade 09/2026 - Ana",
            expira_em_segundos=3600, cliente={"name": "Maria", "taxID": "12345678909"}, splits=splits,
        )

        args, kwargs = mock_request.call_args
        self.assertEqual(args, ("POST", f"{BASE}/api/v1/charge"))
        self.assertEqual(kwargs["params"], {"return_existing": "true"})
        self.assertEqual(kwargs["json"], {
            "correlationID": "c1", "value": 12000, "comment": "Mensalidade 09/2026 - Ana",
            "expiresIn": 3600, "customer": {"name": "Maria", "taxID": "12345678909"}, "splits": splits,
        })
        self.assertEqual(cobranca.br_code, "000201")
        self.assertEqual(cobranca.expira_em.month, 10)

    def test_obter_e_remover_cobranca_codificam_o_correlation_id(self, mock_request):
        mock_request.return_value = resposta(json={"charge": {"correlationID": "a/b", "status": "COMPLETED"}})
        self.assertEqual(self.client.obter_cobranca("a/b").status, "COMPLETED")
        self.assertEqual(mock_request.call_args.args, ("GET", f"{BASE}/api/v1/charge/a%2Fb"))

        mock_request.return_value = resposta(json={"status": "OK"})
        self.client.remover_cobranca("c1")
        self.assertEqual(mock_request.call_args.args, ("DELETE", f"{BASE}/api/v1/charge/c1"))

    # --------------------------------------------------------------- webhook
    def test_chaves_publicas_nao_enviam_o_app_id(self, mock_request):
        mock_request.return_value = resposta(json={"public_keys": [
            {"key": "PEM-ANTIGA", "is_current": False}, {"key": "PEM-NOVA", "is_current": True},
        ]})

        self.assertEqual(self.client.chaves_publicas_webhook(), ["PEM-ANTIGA", "PEM-NOVA"])
        self.assertNotIn("Authorization", mock_request.call_args.kwargs["headers"])


class ProtecaoDaSuiteTests(SimpleTestCase):
    def test_chamada_nao_simulada_nunca_sai_para_a_rede(self):
        from config.test_runner import ChamadaRealProibida

        with self.assertRaises(ChamadaRealProibida):
            WooviClient().obter_cobranca("qualquer")
