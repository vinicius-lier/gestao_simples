from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase

from academias.models import Academia
from atletas.models import Atleta, Responsavel
from financeiro.lembretes import calcular_estagio, enviar_lembretes
from financeiro.models import LembreteCobranca, Mensalidade
from integracoes.whatsapp.client import WhatsAppAPIError
from matriculas.models import Matricula
from modalidades.models import Modalidade

HOJE = date(2026, 9, 6)


class CalcularEstagioTests(TestCase):
    def setUp(self):
        academia = Academia.objects.create(nome="Academia Teste", cnpj="LC1")
        atleta = Atleta.objects.create(academia=academia, nome="Atleta")
        modalidade = Modalidade.objects.create(academia=academia, nome="Judô")
        self.matricula = Matricula.objects.create(
            academia=academia, atleta=atleta, modalidade=modalidade,
            valor_mensalidade=Decimal("120.00"), dia_vencimento=10, data_inicio=date(2020, 1, 1),
        )

    def mensalidade(self, vencimento):
        return Mensalidade(vencimento=vencimento)

    def test_cinco_dias_antes(self):
        m = self.mensalidade(HOJE + timedelta(days=5))
        self.assertEqual(calcular_estagio(m, HOJE), LembreteCobranca.CINCO_DIAS)

    def test_um_dia_antes(self):
        m = self.mensalidade(HOJE + timedelta(days=1))
        self.assertEqual(calcular_estagio(m, HOJE), LembreteCobranca.UM_DIA)

    def test_no_dia(self):
        m = self.mensalidade(HOJE)
        self.assertEqual(calcular_estagio(m, HOJE), LembreteCobranca.VENCIMENTO)

    def test_atrasada(self):
        m = self.mensalidade(HOJE - timedelta(days=1))
        self.assertEqual(calcular_estagio(m, HOJE), LembreteCobranca.ATRASADA)

        m_bem_atrasada = self.mensalidade(HOJE - timedelta(days=40))
        self.assertEqual(calcular_estagio(m_bem_atrasada, HOJE), LembreteCobranca.ATRASADA)

    def test_fora_de_qualquer_janela_nao_gera_estagio(self):
        for dias in (2, 3, 4, 6, 10):
            m = self.mensalidade(HOJE + timedelta(days=dias))
            self.assertIsNone(calcular_estagio(m, HOJE))


class EnviarLembretesTests(TestCase):
    def setUp(self):
        self.academia = Academia.objects.create(nome="Academia Teste", cnpj="EL1")
        self.outra = Academia.objects.create(nome="Outra", cnpj="EL2")
        self.responsavel = Responsavel.objects.create(
            academia=self.academia, nome="Resp", cpf="1", whatsapp="21999998888"
        )
        self.atleta = Atleta.objects.create(
            academia=self.academia, nome="Aluno", responsavel_financeiro=self.responsavel
        )
        self.modalidade = Modalidade.objects.create(academia=self.academia, nome="Judô")
        self.matricula = Matricula.objects.create(
            academia=self.academia, atleta=self.atleta, modalidade=self.modalidade,
            valor_mensalidade=Decimal("120.00"), dia_vencimento=10, data_inicio=date(2020, 1, 1),
        )

    def criar_mensalidade(self, vencimento, status="pendente", responsavel_financeiro=True):
        if not responsavel_financeiro:
            atleta = Atleta.objects.create(academia=self.academia, nome="Sem responsável")
            matricula = Matricula.objects.create(
                academia=self.academia, atleta=atleta, modalidade=self.modalidade,
                valor_mensalidade=Decimal("120.00"), dia_vencimento=10, data_inicio=date(2020, 1, 1),
            )
        else:
            matricula = self.matricula
        return Mensalidade.objects.create(
            academia=self.academia, matricula=matricula, competencia=date(2026, 9, 1),
            valor=Decimal("120.00"), vencimento=vencimento, status=status,
        )

    @patch("integracoes.whatsapp.services.WhatsAppClient")
    def test_envia_lembrete_dentro_da_janela_e_registra(self, mock_client_class):
        mock_client_class.return_value.enviar_template.return_value = {"ok": True}
        mensalidade = self.criar_mensalidade(HOJE + timedelta(days=5))

        enviados = enviar_lembretes(hoje=HOJE)

        self.assertEqual(enviados, [mensalidade.pk])
        mock_client_class.return_value.enviar_template.assert_called_once()
        self.assertTrue(
            LembreteCobranca.objects.filter(
                mensalidade=mensalidade, estagio=LembreteCobranca.CINCO_DIAS
            ).exists()
        )

    @patch("integracoes.whatsapp.services.WhatsAppClient")
    def test_link_enviado_leva_direto_para_pagamento(self, mock_client_class):
        mock_client_class.return_value.enviar_template.return_value = {"ok": True}
        mensalidade = self.criar_mensalidade(HOJE)

        enviar_lembretes(hoje=HOJE)

        kwargs = mock_client_class.return_value.enviar_template.call_args.kwargs
        link = kwargs["parametros"][-1]
        self.assertIn(f"/responsavel/mensalidade/{mensalidade.pk}/pagar/", link)
        self.assertTrue(link.startswith("http"))

    @patch("integracoes.whatsapp.services.WhatsAppClient")
    def test_nao_reenvia_o_mesmo_estagio(self, mock_client_class):
        mock_client_class.return_value.enviar_template.return_value = {"ok": True}
        self.criar_mensalidade(HOJE + timedelta(days=1))

        enviar_lembretes(hoje=HOJE)
        enviar_lembretes(hoje=HOJE)

        mock_client_class.return_value.enviar_template.assert_called_once()
        self.assertEqual(LembreteCobranca.objects.count(), 1)

    @patch("integracoes.whatsapp.services.WhatsAppClient")
    def test_atrasada_so_dispara_uma_vez_mesmo_passando_os_dias(self, mock_client_class):
        mock_client_class.return_value.enviar_template.return_value = {"ok": True}
        self.criar_mensalidade(HOJE - timedelta(days=1), status="vencida")

        enviar_lembretes(hoje=HOJE)
        enviar_lembretes(hoje=HOJE + timedelta(days=10))

        mock_client_class.return_value.enviar_template.assert_called_once()
        self.assertEqual(LembreteCobranca.objects.count(), 1)

    def test_fora_da_janela_nao_envia_nada(self):
        self.criar_mensalidade(HOJE + timedelta(days=3))

        enviados = enviar_lembretes(hoje=HOJE)

        self.assertEqual(enviados, [])
        self.assertFalse(LembreteCobranca.objects.exists())

    def test_mensalidade_paga_nao_recebe_lembrete(self):
        self.criar_mensalidade(HOJE, status="paga")

        enviados = enviar_lembretes(hoje=HOJE)

        self.assertEqual(enviados, [])

    def test_sem_responsavel_financeiro_nao_quebra_e_nao_marca_enviado(self):
        self.criar_mensalidade(HOJE, responsavel_financeiro=False)

        enviados = enviar_lembretes(hoje=HOJE)

        self.assertEqual(enviados, [])
        self.assertFalse(LembreteCobranca.objects.exists())

    @patch("integracoes.whatsapp.services.WhatsAppClient")
    def test_falha_no_envio_nao_marca_como_enviado_e_tenta_de_novo_depois(self, mock_client_class):
        mock_client_class.return_value.enviar_template.side_effect = WhatsAppAPIError("erro")
        self.criar_mensalidade(HOJE)

        primeira = enviar_lembretes(hoje=HOJE)
        self.assertEqual(primeira, [])
        self.assertFalse(LembreteCobranca.objects.exists())

        mock_client_class.return_value.enviar_template.side_effect = None
        mock_client_class.return_value.enviar_template.return_value = {"ok": True}
        segunda = enviar_lembretes(hoje=HOJE)
        self.assertEqual(len(segunda), 1)

    @patch("integracoes.whatsapp.services.WhatsAppClient")
    def test_isola_por_academia(self, mock_client_class):
        mock_client_class.return_value.enviar_template.return_value = {"ok": True}
        outro_responsavel = Responsavel.objects.create(
            academia=self.outra, nome="Resp outra", cpf="2", whatsapp="21988887777"
        )
        outro_atleta = Atleta.objects.create(
            academia=self.outra, nome="Aluno outra", responsavel_financeiro=outro_responsavel
        )
        outra_modalidade = Modalidade.objects.create(academia=self.outra, nome="Karatê")
        outra_matricula = Matricula.objects.create(
            academia=self.outra, atleta=outro_atleta, modalidade=outra_modalidade,
            valor_mensalidade=Decimal("80.00"), dia_vencimento=10, data_inicio=date(2020, 1, 1),
        )
        Mensalidade.objects.create(
            academia=self.outra, matricula=outra_matricula, competencia=date(2026, 9, 1),
            valor=Decimal("80.00"), vencimento=HOJE, status="pendente",
        )
        mensalidade_a = self.criar_mensalidade(HOJE)

        enviados = enviar_lembretes(hoje=HOJE, academia=self.academia)

        self.assertEqual(enviados, [mensalidade_a.pk])
        mock_client_class.return_value.enviar_template.assert_called_once()
