"""Cenário comum dos testes da integração Woovi (sem chamadas reais)."""
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.utils import timezone

from academias.models import Academia
from atletas.models import Atleta, Responsavel
from financeiro.models import CobrancaPix, ContaRecebimento, Mensalidade
from integracoes.woovi.client import Cobranca, Saque, Subconta
from matriculas.models import Matricula
from modalidades.models import Modalidade

CHAVE = "escola@keiko.com.br"


class CenarioWoovi:
    def criar_cenario(self):
        self.academia = Academia.objects.create(nome="Keiko Fukuda LTDA", nome_fantasia="Escola de Judô Keiko Fukuda", cnpj="WV1")
        self.usuario = get_user_model().objects.create_user("dona", password="senha123")
        self.responsavel = Responsavel.objects.create(
            academia=self.academia, nome="Maria", cpf="529.982.247-25", whatsapp="(21) 99999-8888",
        )
        self.atleta = Atleta.objects.create(academia=self.academia, nome="Ana", responsavel_financeiro=self.responsavel)
        self.matricula = Matricula.objects.create(
            academia=self.academia, atleta=self.atleta,
            modalidade=Modalidade.objects.create(academia=self.academia, nome="Judô"),
            valor_mensalidade=Decimal("120.00"), dia_vencimento=10, data_inicio=date(2026, 1, 1),
        )
        self.mensalidade = self.nova_mensalidade(date(2026, 9, 1))
        self.conta = ContaRecebimento.objects.create(
            academia=self.academia, tipo_chave=ContaRecebimento.EMAIL, pix_key=CHAVE,
        )

    def nova_mensalidade(self, competencia, status="pendente"):
        return Mensalidade.objects.create(
            academia=self.academia, matricula=self.matricula, competencia=competencia,
            valor=Decimal("120.00"), vencimento=competencia.replace(day=10), status=status,
        )

    def usar_conta_propria(self):
        self.conta.ativa = False
        self.conta.save(update_fields=["ativa"])
        self.conta = ContaRecebimento.objects.create(
            academia=self.academia, modelo_recebimento=ContaRecebimento.CONTA_PROPRIA,
            status=ContaRecebimento.CONECTADA, credencial_ref=f"WOOVI_ACADEMIA_{self.academia.pk}_APP_ID",
            provider_account_id="conta-teste", api_base_url="https://api.woovi-sandbox.com",
        )
        return self.conta

    def cobranca(self, mensalidade=None, conta=None, correlation_id="mensalidade-1-abc", dias=10, **extra):
        return CobrancaPix.objects.create(
            mensalidade=mensalidade or self.mensalidade,
            conta_recebimento=conta or self.conta,
            correlation_id=correlation_id,
            valor=Decimal("120.00"),
            br_code="00020126PIX",
            expira_em=timezone.now() + timedelta(days=dias),
            **extra,
        )


def cobranca_criada(**kw):
    return Cobranca(
        correlation_id=kw["correlation_id"], status="ACTIVE", valor_centavos=kw["valor_centavos"],
        br_code="00020126PIX", link_pagamento="https://woovi.com/pay/x",
        expira_em=timezone.now() + timedelta(days=30), transaction_id="", pago_em=None,
    )


def subconta(saldo=0, bloqueada=False, chave=CHAVE):
    return Subconta(pix_key=chave, nome="Escola de Judô Keiko Fukuda", saldo_centavos=saldo, saque_bloqueado=bloqueada)


def saque(valor, status="CREATED", correlation_id="saque-1", e2e=""):
    return Saque(status=status, valor_centavos=valor, correlation_id=correlation_id, end_to_end_id=e2e, destino=CHAVE)
