"""O smoke fala com a Woovi real só quando rodado à mão. Aqui o cliente é
sempre simulado; o executor de testes bloqueia qualquer chamada que escape."""
import os
from io import StringIO
from unittest.mock import patch

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings

from financeiro.management.commands.smoke_woovi import mascarar_chave
from integracoes.woovi.client import Cobranca
from integracoes.woovi.exceptions import WooviAuthError, WooviNotFoundError
from tests.woovi_base import subconta

CHAVE = "escola@keiko.com.br"
SANDBOX = "https://api.woovi-sandbox.com"
PRODUCAO = "https://api.woovi.com"


def respostas(*itens):
    """input() que devolve as respostas em ordem e depois simula o fim da entrada."""
    fila = list(itens)

    def _input(_prompt=""):
        if not fila:
            raise EOFError
        return fila.pop(0)

    return _input


@patch("integracoes.woovi.client.WooviClient")
class SmokeWooviTests(SimpleTestCase):
    def rodar(self, *entradas, base=SANDBOX, args=(), falha=False):
        saida = StringIO()
        with override_settings(WOOVI_BASE_URL=base), \
                patch.dict(os.environ, {"WOOVI_BASE_URL": base}), \
                patch("builtins.input", respostas(*entradas)):
            if falha:
                with self.assertRaisesMessage(CommandError, "Smoke terminou com falha"):
                    call_command("smoke_woovi", *args, stdout=saida)
            else:
                call_command("smoke_woovi", *args, stdout=saida)
        return saida.getvalue()

    def preparar(self, cliente):
        api = cliente.return_value
        api.base_url = SANDBOX
        api.listar_subcontas.return_value = [subconta(saldo=1250, chave=CHAVE)]
        api.obter_subconta.return_value = subconta(saldo=1250, chave=CHAVE)
        api.criar_ou_obter_subconta.return_value = subconta(saldo=1250, chave=CHAVE)
        return api

    # ----------------------------------------------------------- fase 1
    def test_sem_base_url_explicita_aborta_sem_rede(self, cliente):
        ambiente = {k: v for k, v in os.environ.items() if k != "WOOVI_BASE_URL"}
        with patch.dict(os.environ, ambiente, clear=True), self.assertRaisesMessage(CommandError, "WOOVI_BASE_URL"):
            call_command("smoke_woovi", stdout=StringIO())
        cliente.assert_not_called()

    def test_sem_app_id_aborta_sem_rede(self, cliente):
        with override_settings(WOOVI_APP_ID=""), patch.dict(os.environ, {"WOOVI_BASE_URL": SANDBOX}), \
                self.assertRaisesMessage(CommandError, "WOOVI_APP_ID"):
            call_command("smoke_woovi", stdout=StringIO())
        cliente.assert_not_called()

    def test_producao_exige_confirmacao_antes_de_qualquer_chamada(self, cliente):
        api = self.preparar(cliente)
        saida = self.rodar("n", base=PRODUCAO)
        self.assertIn("ATENÇÃO: AMBIENTE REAL / PRODUÇÃO", saida)
        api.listar_subcontas.assert_not_called()
        self.assertIn("Autenticação Woovi: NÃO TESTADA", saida)

    # ------------------------------------------------------- fases 2 e 3
    def test_consulta_sem_operacoes_mutaveis(self, cliente):
        api = self.preparar(cliente)

        saida = self.rodar(CHAVE, "n", "n")

        api.obter_subconta.assert_called_with(CHAVE)
        api.criar_ou_obter_subconta.assert_not_called()
        api.criar_cobranca.assert_not_called()
        api.sacar_subconta.assert_not_called()
        self.assertIn("SANDBOX", saida)
        self.assertIn("es***@keiko.com.br", saida)
        self.assertNotIn(CHAVE, saida)
        for linha in ("Configuração: OK", "Autenticação Woovi: OK", "Consulta subconta: OK",
                      "Saque bloqueado: NÃO", "Saldo disponível: R$ 12,50", "POST subconta: PULADO",
                      "Cobrança de teste: PULADA", "Withdraw: NÃO EXECUTADO"):
            self.assertIn(linha, saida)

    def test_app_id_nunca_aparece_na_saida(self, cliente):
        api = self.preparar(cliente)
        api.criar_cobranca.side_effect = lambda **kw: Cobranca(
            kw["correlation_id"], "ACTIVE", kw["valor_centavos"], "000201PIX", "", None, "", None,
        )
        saida = self.rodar(CHAVE, "s", "s", "5,00", "s")
        self.assertIn("Cobrança de teste: CRIADA", saida)
        self.assertNotIn(settings.WOOVI_APP_ID, saida)
        self.assertNotIn("Authorization", saida)

    def test_appid_recusado_para_e_orienta(self, cliente):
        api = self.preparar(cliente)
        api.listar_subcontas.side_effect = WooviAuthError("A Woovi retornou o status HTTP 401.", 401)

        saida = self.rodar(CHAVE, falha=True)

        self.assertIn("Autenticação Woovi: ERRO", saida)
        self.assertIn("MESMO ambiente", saida)
        api.obter_subconta.assert_not_called()

    def test_subconta_de_outro_ambiente_nao_encontrada(self, cliente):
        api = self.preparar(cliente)
        api.obter_subconta.side_effect = WooviNotFoundError("HTTP 400: Subaccount not found", 400)
        saida = self.rodar(CHAVE, falha=True)
        self.assertIn("Consulta subconta: ERRO", saida)
        self.assertIn("não encontrada no ambiente sandbox", saida)

    def test_sem_terminal_interativo_para_antes_da_consulta(self, cliente):
        api = self.preparar(cliente)
        saida = self.rodar()  # nenhuma resposta: input() recebe EOF
        api.obter_subconta.assert_not_called()
        self.assertIn("Consulta subconta: NÃO EXECUTADA", saida)

    # ------------------------------------------------------------- fase 4
    def test_post_de_subconta_so_com_confirmacao(self, cliente):
        api = self.preparar(cliente)

        saida = self.rodar(CHAVE, "s", "n")

        api.criar_ou_obter_subconta.assert_called_once_with(CHAVE, "Escola de Judô Keiko Fukuda")
        self.assertIn("sem duplicação", saida)
        self.assertIn("POST subconta: EXECUTADO", saida)

    # ------------------------------------------------------------- fase 5
    def test_cobranca_exige_segunda_confirmacao(self, cliente):
        api = self.preparar(cliente)
        saida = self.rodar(CHAVE, "n", "s", "5,00", "n")
        api.criar_cobranca.assert_not_called()
        self.assertIn("Cobrança de teste: PULADA", saida)

    def test_cobranca_de_teste_sem_split(self, cliente):
        api = self.preparar(cliente)
        api.criar_cobranca.side_effect = lambda **kw: Cobranca(
            kw["correlation_id"], "ACTIVE", kw["valor_centavos"], "000201PIX", "https://woovi.com/pay/x", None, "", None,
        )

        saida = self.rodar(CHAVE, "n", "s", "5.00", "s")

        kwargs = api.criar_cobranca.call_args.kwargs
        self.assertEqual(kwargs["valor_centavos"], 500)
        self.assertNotIn("splits", kwargs)
        self.assertRegex(kwargs["correlation_id"], r"^smoke-woovi-\d{8}-\d{6}-[0-9a-f]{6}$")
        self.assertIn("Cobrança de teste: CRIADA", saida)
        self.assertIn(kwargs["correlation_id"], saida)
        self.assertIn("000201PIX", saida)
        api.sacar_subconta.assert_not_called()

    def test_valor_invalido_e_rejeitado(self, cliente):
        api = self.preparar(cliente)
        saida = self.rodar(CHAVE, "n", "s", "abc", "0,50", "999,00")
        api.criar_cobranca.assert_not_called()
        self.assertIn("Valor inválido.", saida)

    # ------------------------------------------------------------- fase 6
    def test_allow_withdraw_ainda_recusa_sacar(self, cliente):
        api = self.preparar(cliente)
        saida = self.rodar(CHAVE, "n", "n", args=["--allow-withdraw"])
        api.sacar_subconta.assert_not_called()
        self.assertIn("ainda NÃO habilitado", saida)
        self.assertIn("Withdraw: NÃO EXECUTADO", saida)

    def test_mostra_o_que_seria_sacado(self, cliente):
        self.preparar(cliente)
        saida = self.rodar(CHAVE, "n", "n")
        # Saldo de R$ 12,50 menos a tarifa de saque de R$ 1,00.
        self.assertIn("Valor que o repasse sacaria agora: R$ 11,50", saida)
        self.assertIn('/withdraw {"value": 1150}', saida)


class MascararChaveTests(SimpleTestCase):
    def test_mascaras(self):
        self.assertEqual(mascarar_chave("escola@keiko.com.br"), "es***@keiko.com.br")
        self.assertEqual(mascarar_chave("52998224725"), "529******25")
        self.assertEqual(mascarar_chave("+5521999998888"), "+55*********88")
        self.assertEqual(
            mascarar_chave("9134e286-6f71-427a-bf00-241681624587"), "913" + "*" * 29 + "4587",
        )
