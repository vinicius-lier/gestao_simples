"""Smoke test REAL da integração com a Woovi — só para rodar à mão.

    python manage.py smoke_woovi [--allow-withdraw]

Fala com a Woovi de verdade (a do WOOVI_BASE_URL do .env), usando o próprio
WooviClient do projeto. Não grava nada no banco. Nada é chamado por testes,
deploy ou inicialização: só existe como comando manual.

Fases: 1) configuração (sem rede); 2) autenticação (GET da lista de
subcontas); 3) consulta da subconta informada; 4) POST de subconta com a
mesma chave — só com confirmação; 5) cobrança Pix com split — só com duas
confirmações; 6) saque: apenas mostra o que seria sacado, NUNCA saca nesta
versão; 7) resumo.

O AppID nunca é impresso: ele só existe no header montado dentro do cliente.
"""
import os
import secrets
import sys
from datetime import datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

HOST_PRODUCAO = "api.woovi.com"
HOST_SANDBOX = "api.woovi-sandbox.com"

# O saque real será liberado numa etapa posterior. Até lá, nem com
# --allow-withdraw o comando chama POST /withdraw.
SAQUE_REAL_HABILITADO = False

VALOR_MINIMO_TESTE = Decimal("1.00")
VALOR_MAXIMO_TESTE = Decimal("50.00")


class _Parar(Exception):
    """Interrompe as fases seguintes (o resumo ainda é impresso)."""


def mascarar_chave(chave):
    """Chave Pix parcialmente escondida para exibição."""
    chave = (chave or "").strip()
    if "@" in chave:
        usuario, dominio = chave.split("@", 1)
        return f"{usuario[:2]}***@{dominio}"
    if len(chave) <= 6:
        return "*" * len(chave)
    fim = 2 if len(chave) <= 14 else 4
    return chave[:3] + "*" * (len(chave) - 3 - fim) + chave[-fim:]


def reais(centavos):
    valor = f"{Decimal(centavos) / 100:,.2f}"
    return "R$ " + valor.replace(",", "X").replace(".", ",").replace("X", ".")


def ambiente_da(base_url):
    host = (urlparse(base_url).hostname or "").lower()
    if host == HOST_SANDBOX:
        return "sandbox"
    if host == HOST_PRODUCAO:
        return "producao"
    return "desconhecido"


class Command(BaseCommand):
    help = (
        "Smoke test REAL da Woovi (manual). Consulta autenticação e subconta; "
        "POST de subconta e cobrança só com confirmação; nunca saca."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--allow-withdraw",
            action="store_true",
            help="Reservado para a etapa de saque real. Nesta versão o comando ainda recusa sacar.",
        )

    # ------------------------------------------------------------ utilidades
    def _linha(self, texto="", estilo=None):
        self.stdout.write(estilo(texto) if estilo else texto)

    def _perguntar(self, texto):
        """input() que trata falta de terminal interativo como 'não'."""
        self.stdout.flush()
        try:
            return input(texto).strip()
        except EOFError:
            self._linha("\n(sem entrada interativa — nada foi respondido)", self.style.WARNING)
            return None

    def _confirmar(self, texto):
        resposta = self._perguntar(f"{texto} [s/N] ")
        return (resposta or "").lower() in ("s", "sim")

    # ------------------------------------------------------------- execução
    def handle(self, *args, **opts):
        self.resumo = {
            "Configuração": "ERRO",
            "Autenticação Woovi": "NÃO TESTADA",
            "Consulta subconta": "NÃO EXECUTADA",
            "Saque bloqueado": "—",
            "Saldo disponível": "—",
            "POST subconta": "PULADO",
            "Cobrança de teste": "PULADA",
            "Withdraw": "NÃO EXECUTADO",
        }
        self.cobranca_criada = None
        try:
            client = self._fase_configuracao()
            subcontas = self._fase_autenticacao(client)
            subconta = self._fase_consulta(client, subcontas)
            self._fase_post_subconta(client, subconta, len(subcontas))
            self._fase_cobranca(client, subconta)
            self._fase_saque(client, subconta, opts["allow_withdraw"])
        except _Parar:
            pass
        finally:
            self._fase_resumo()

        falhas = [nome for nome, valor in self.resumo.items() if "ERRO" in valor or "DESCONHECIDO" in valor]
        if falhas:
            raise CommandError("Smoke terminou com falha em: " + ", ".join(falhas))

    def _fase_configuracao(self):
        from integracoes.woovi.client import WooviClient
        from integracoes.woovi.exceptions import WooviConfigError

        self._linha("== FASE 1 — Configuração", self.style.MIGRATE_HEADING)
        base_url = (getattr(settings, "WOOVI_BASE_URL", "") or "").strip()
        if "WOOVI_BASE_URL" not in os.environ or not base_url:
            raise CommandError(
                "WOOVI_BASE_URL não está definida no ambiente/.env. Defina explicitamente "
                "(o padrão do código é produção). Nenhuma chamada foi feita."
            )
        if not getattr(settings, "WOOVI_APP_ID", ""):
            raise CommandError("WOOVI_APP_ID não está definido. Nenhuma chamada foi feita.")
        try:
            client = WooviClient()
        except WooviConfigError as exc:
            raise CommandError(f"{exc} Nenhuma chamada foi feita.") from None

        self.ambiente = ambiente_da(base_url)
        rotulo = {
            "sandbox": "SANDBOX (testes — não movimenta dinheiro real)",
            "producao": "PRODUÇÃO (dinheiro real)",
            "desconhecido": "DESCONHECIDO — tratado como PRODUÇÃO",
        }[self.ambiente]
        self._linha(f"Base URL: {client.base_url}")
        self._linha(f"Ambiente: {rotulo}")
        self._linha("WOOVI_APP_ID: presente (valor não exibido)")
        self.resumo["Configuração"] = "OK"

        if self.ambiente != "sandbox":
            self._linha("")
            self._linha("=" * 64, self.style.ERROR)
            self._linha("  ATENÇÃO: AMBIENTE REAL / PRODUÇÃO", self.style.ERROR)
            self._linha("  Cobranças e saques aqui movimentam dinheiro de verdade.", self.style.ERROR)
            self._linha("=" * 64, self.style.ERROR)
            if not self._confirmar("Continuar com consultas SOMENTE LEITURA na produção?"):
                self._linha("Encerrado antes de qualquer chamada à Woovi.")
                raise _Parar
        return client

    def _fase_autenticacao(self, client):
        from integracoes.woovi.exceptions import (
            WooviAuthError,
            WooviInvalidResponseError,
            WooviNotFoundError,
            WooviTimeoutError,
            WooviUnavailableError,
        )

        self._linha("")
        self._linha("== FASE 2 — Autenticação (GET /api/v1/subaccount)", self.style.MIGRATE_HEADING)
        motivo = None
        try:
            subcontas = client.listar_subcontas()
        except WooviTimeoutError:
            motivo = "tempo esgotado: a Woovi não respondeu."
        except WooviAuthError as exc:
            motivo = (
                f"AppID recusado (HTTP {exc.status_code}). Confira se o AppID é do MESMO "
                f"ambiente da URL ({self.ambiente}) e se está ativo no painel."
            )
        except WooviNotFoundError as exc:
            motivo = f"endpoint não encontrado (HTTP {exc.status_code}). Confira WOOVI_BASE_URL."
        except WooviUnavailableError as exc:
            motivo = f"Woovi indisponível ou falha de conexão ({exc})."
        except WooviInvalidResponseError as exc:
            motivo = f"resposta fora do formato esperado ({exc})."
        if motivo:
            self.resumo["Autenticação Woovi"] = "ERRO"
            self._linha(f"ERRO — {motivo}", self.style.ERROR)
            raise _Parar

        self.resumo["Autenticação Woovi"] = "OK"
        self._linha(f"OK — AppID aceito. Subcontas visíveis nesta conta: {len(subcontas)}", self.style.SUCCESS)
        return subcontas

    def _fase_consulta(self, client, subcontas):
        from integracoes.woovi.exceptions import WooviError, WooviNotFoundError

        self._linha("")
        self._linha("== FASE 3 — Subconta existente (GET /api/v1/subaccount/{pixKey})", self.style.MIGRATE_HEADING)
        chave = self._perguntar("Chave Pix da subconta criada no painel: ")
        if not chave:
            self._linha("Nenhuma chave informada. Encerrado antes da consulta.")
            raise _Parar

        try:
            subconta = client.obter_subconta(chave)
        except WooviNotFoundError:
            self.resumo["Consulta subconta"] = "ERRO"
            self._linha(
                f"Subconta {mascarar_chave(chave)} não encontrada no ambiente {self.ambiente}. "
                "Se ela foi criada no painel do outro ambiente (sandbox x produção), isso é esperado.",
                self.style.ERROR,
            )
            raise _Parar
        except WooviError as exc:
            self.resumo["Consulta subconta"] = "ERRO"
            self._linha(f"ERRO ao consultar: {exc}", self.style.ERROR)
            raise _Parar

        na_lista = any(s.pix_key == subconta.pix_key for s in subcontas)
        self.resumo["Consulta subconta"] = "OK"
        self.resumo["Saque bloqueado"] = "SIM" if subconta.saque_bloqueado else "NÃO"
        self.resumo["Saldo disponível"] = reais(subconta.saldo_centavos)
        self._linha(f"Nome: {subconta.nome or '—'}", self.style.SUCCESS)
        self._linha(f"Chave Pix: {mascarar_chave(subconta.pix_key)}")
        self._linha(f"Saldo: {reais(subconta.saldo_centavos)}")
        self._linha(f"Saque bloqueado (withdrawBlocked): {self.resumo['Saque bloqueado']}")
        self._linha(f"Aparece também na listagem da fase 2: {'sim' if na_lista else 'NÃO'}")
        self._linha("Confira no painel: nome, saldo e 'Saque Bloqueado' devem bater com os valores acima.")
        return subconta

    def _fase_post_subconta(self, client, subconta, total_antes):
        from integracoes.woovi.exceptions import WooviError

        self._linha("")
        self._linha("== FASE 4 — POST /api/v1/subaccount com a MESMA chave", self.style.MIGRATE_HEADING)
        self._linha("Esperado: a Woovi recuperar a subconta existente, sem duplicar nem renomear.")
        if not self._confirmar("Executar o POST com a mesma chave e o mesmo nome?"):
            self._linha("Pulado.")
            return
        try:
            recuperada = client.criar_ou_obter_subconta(subconta.pix_key, subconta.nome)
            total_depois = len(client.listar_subcontas())
        except WooviError as exc:
            self.resumo["POST subconta"] = "ERRO"
            self._linha(f"ERRO no POST: {exc}", self.style.ERROR)
            return
        self.resumo["POST subconta"] = "EXECUTADO"
        mesma = recuperada.pix_key == subconta.pix_key
        self._linha(f"Resposta: chave {mascarar_chave(recuperada.pix_key)} ({'a mesma' if mesma else 'DIFERENTE'})")
        self._linha(f"Nome retornado: {recuperada.nome or '—'}")
        self._linha(f"Saldo retornado: {reais(recuperada.saldo_centavos)}")
        self._linha(
            f"Subcontas na conta: antes {total_antes}, depois {total_depois} "
            f"({'sem duplicação' if total_depois == total_antes else 'MUDOU — conferir no painel'})",
            self.style.SUCCESS if total_depois == total_antes and mesma else self.style.ERROR,
        )

    def _ler_valor(self):
        for _ in range(3):
            texto = self._perguntar(
                f"Valor em reais ({VALOR_MINIMO_TESTE:.2f} a {VALOR_MAXIMO_TESTE:.2f}; sugestão: 2,00 ou 5,00): "
            )
            if texto is None:
                return None
            texto = texto.replace("R$", "").strip()
            if "," in texto:  # formato brasileiro: 1.234,56
                texto = texto.replace(".", "").replace(",", ".")
            try:
                valor = Decimal(texto)
            except InvalidOperation:
                valor = None
            if valor is not None and VALOR_MINIMO_TESTE <= valor <= VALOR_MAXIMO_TESTE and valor == valor.quantize(Decimal("0.01")):
                return valor
            self._linha("Valor inválido.", self.style.WARNING)
        return None

    def _fase_cobranca(self, client, subconta):
        from integracoes.woovi.exceptions import WooviError, WooviTimeoutError
        from integracoes.woovi.services import valor_em_centavos

        self._linha("")
        self._linha("== FASE 5 — Cobrança Pix de teste (POST /api/v1/charge?return_existing=true)", self.style.MIGRATE_HEADING)
        if not self._confirmar("Deseja criar uma cobrança REAL de teste?"):
            self._linha("Pulada.")
            return
        valor = self._ler_valor()
        if valor is None:
            self._linha("Sem valor válido. Pulada.")
            return

        centavos = valor_em_centavos(valor)
        correlation_id = f"smoke-woovi-{datetime.now():%Y%m%d-%H%M%S}-{secrets.token_hex(3)}"
        self._linha("Resumo da cobrança:")
        self._linha(f"  Ambiente: {self.ambiente}")
        self._linha(f"  Valor: {reais(centavos)}")
        self._linha(
            "  Sem split: o valor entra na conta principal; no fluxo do ERP, o líquido é "
            f"creditado depois na subconta {mascarar_chave(subconta.pix_key)}."
        )
        self._linha(f"  correlationID: {correlation_id}")
        self._linha("  Validade: 24 horas")
        if not self._confirmar("CONFIRMAR criação de cobrança real?"):
            self._linha("Pulada.")
            return

        try:
            cobranca = client.criar_cobranca(
                correlation_id=correlation_id,
                valor_centavos=centavos,
                comentario="Teste Gestão Simples (smoke)",
                expira_em_segundos=24 * 60 * 60,
            )
        except WooviTimeoutError:
            self.resumo["Cobrança de teste"] = "RESULTADO DESCONHECIDO"
            self._linha(
                f"Tempo esgotado: a cobrança pode ter sido criada. Procure {correlation_id} no painel "
                "antes de tentar de novo.",
                self.style.ERROR,
            )
            return
        except WooviError as exc:
            self.resumo["Cobrança de teste"] = "ERRO"
            self._linha(f"ERRO ao criar a cobrança: {exc}", self.style.ERROR)
            return

        self.cobranca_criada = cobranca
        self.resumo["Cobrança de teste"] = "CRIADA"
        self._linha(f"correlationID: {cobranca.correlation_id}", self.style.SUCCESS)
        self._linha(f"Status: {cobranca.status}")
        self._linha(f"Valor: {reais(cobranca.valor_centavos)}")
        self._linha(f"Link de pagamento: {cobranca.link_pagamento or '—'}")
        self._linha(f"Pix copia e cola (brCode): {cobranca.br_code or '—'}")

    def _fase_saque(self, client, subconta, permitir_saque):
        from integracoes.woovi.client import WooviClient
        from integracoes.woovi.exceptions import WooviError
        from integracoes.woovi.repasses import saque_minimo_centavos, valor_do_saque

        self._linha("")
        self._linha("== FASE 6 — Saque (somente leitura: POST /withdraw NÃO é chamado)", self.style.MIGRATE_HEADING)
        try:
            atual = client.obter_subconta(subconta.pix_key)
        except WooviError as exc:
            self._linha(f"Não foi possível reler o saldo: {exc}", self.style.WARNING)
            atual = subconta
        minimo = saque_minimo_centavos()
        disponivel = valor_do_saque(atual.saldo_centavos)
        disponivel = disponivel if disponivel >= minimo else 0
        self.resumo["Saque bloqueado"] = "SIM" if atual.saque_bloqueado else "NÃO"
        self.resumo["Saldo disponível"] = reais(atual.saldo_centavos)
        self._linha(f"Saldo atual: {reais(atual.saldo_centavos)}")
        self._linha(f"Saque bloqueado: {self.resumo['Saque bloqueado']}")
        self._linha(
            f"Valor que o repasse sacaria agora: {reais(disponivel)}"
            + ("" if disponivel else f" (abaixo do mínimo de {reais(minimo)}, contando a tarifa, ou sem saldo)")
        )
        self._linha(
            f"Método disponível no cliente: WooviClient.sacar_subconta(pix_key, valor_centavos) — "
            f"{'sim' if callable(getattr(WooviClient, 'sacar_subconta', None)) else 'NÃO'}"
        )
        self._linha(
            f"Requisição que seria feita: POST /api/v1/subaccount/{mascarar_chave(atual.pix_key)}/withdraw "
            f'{{"value": {disponivel}}} (saldo menos a tarifa de saque)'
        )

        if permitir_saque:
            self._linha(f"--allow-withdraw: valor {reais(disponivel)} para {mascarar_chave(atual.pix_key)}.")
            if not SAQUE_REAL_HABILITADO:
                self._linha(
                    "Saque real ainda NÃO habilitado nesta versão do smoke: será liberado numa etapa "
                    "posterior. Nenhuma chamada ao /withdraw foi feita.",
                    self.style.WARNING,
                )
                return
            if atual.saque_bloqueado or not disponivel:
                self._linha("Nada a sacar (bloqueado ou sem saldo).")
                return
            if self._confirmar("CONFIRMAR SAQUE REAL deste saldo?"):
                saque = client.sacar_subconta(atual.pix_key, disponivel)
                self.resumo["Withdraw"] = f"EXECUTADO ({saque.status})"

    def _fase_resumo(self):
        self._linha("")
        self._linha("== RESUMO", self.style.MIGRATE_HEADING)
        for nome, valor in self.resumo.items():
            self._linha(f"- {nome}: {valor}")
        if self.cobranca_criada is not None:
            self._linha("")
            self._linha(f"Cobrança de teste: {self.cobranca_criada.correlation_id}")
            self._linha("No painel da Woovi: Cobranças → busque pelo correlationID acima.")
            self._linha(
                "Próximo passo: pague manualmente e rode o smoke de novo para ver se o saldo "
                "chegou à subconta (fase 3/6)."
            )
        sys.stdout.flush()
