from datetime import date
from decimal import Decimal
import tempfile
import shutil

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from unittest.mock import patch
from django.utils import timezone

from academias.models import Unidade
from atletas.models import Atleta, DocumentoAtleta
from financeiro.models import Mensalidade
from financeiro.services import _vencimento, proximo_vencimento, gerar_mensalidades
from matriculas.models import Matricula
from modalidades.models import Turma
from .models import CampanhaRecadastramento, PedidoRecadastramento
from .test_matricula_convite import BaseConvite
from .test_recadastramento import imagem
from tests.woovi_base import CenarioWoovi, cobranca_criada


def laudo():
    return SimpleUploadedFile('laudo.pdf', b'%PDF-1.4\nidentidade', content_type='application/pdf')


class AjustesEscolaTests(BaseConvite):
    def setUp(self):
        super().setUp()
        pasta = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, pasta, ignore_errors=True)
        config = override_settings(MEDIA_ROOT=pasta)
        config.enable()
        self.addCleanup(config.disable)
        self.a.dia_vencimento_fixo = 15
        self.a.save()

    def test_vencimento_fixo_ignora_escolha_e_anexa_laudo(self):
        convite = self.convite()
        dados = self.dados_familia(vencimento_preferido='5', endereco='Rua A, 10, Centro, Seropédica/RJ', documento_laudo=laudo(), foto=imagem())
        resposta = self.client.post(convite.url(), dados)
        self.assertEqual(resposta.status_code, 302)
        aluno = Atleta.objects.get()
        self.assertEqual(aluno.endereco, dados['endereco'])
        self.assertTrue(aluno.foto)
        self.assertEqual(DocumentoAtleta.objects.get().tipo, 'laudo')
        matricula = Matricula.objects.get()
        self.assertEqual(matricula.dia_vencimento, 15)
        matricula.primeiro_vencimento = date(2027, 1, 20)
        self.assertEqual(_vencimento(matricula, 2027, 1), date(2027, 1, 15))
        self.assertEqual(proximo_vencimento(matricula, date(2027, 1, 16)), date(2027, 2, 15))

    def test_maior_dispensa_autorizacao_menor_exige(self):
        convite = self.convite()
        dados = self.dados_familia(data_nascimento='1990-01-01', autorizados_buscar='')
        self.assertEqual(self.client.post(convite.url(), dados).status_code, 302)
        outro = self.convite()
        dados = self.dados_familia(autorizados_buscar='')
        resposta = self.client.post(outro.url(), dados)
        self.assertIn('autorizados_buscar', resposta.context['form'].errors)

    def test_recadastro_pede_rg_endereco_e_saude_sem_outros_documentos(self):
        aluno = Atleta.objects.create(academia=self.a, nome='Aluno')
        campanha = CampanhaRecadastramento.objects.create(academia=self.a, titulo='Atualizar', prazo=date(2099,1,1), pedir_foto=False,
            documentos_pedidos='atestado,residencia,federacao')
        pedido = PedidoRecadastramento.objects.create(academia=self.a, campanha=campanha, atleta=aluno)
        resposta = self.client.get(pedido.url())
        campos = resposta.context['form'].fields
        self.assertIn('documento_laudo', campos)
        self.assertNotIn('documento_identidade', campos)
        self.assertNotIn('documento_atestado', campos)
        self.assertNotIn('documento_residencia', campos)
        self.assertNotIn('documento_federacao', campos)
        dados = self.dados_familia()
        dados.pop('vencimento_preferido')
        resposta = self.client.post(pedido.url(), dados)
        self.assertIn('endereco', resposta.context['form'].errors)
        self.assertIn('rg', resposta.context['form'].errors)
        self.assertNotIn('documento_laudo', resposta.context['form'].errors)
        dados.update(endereco='Rua A, 10, Seropédica/RJ', rg='00123456')
        resposta = self.client.post(pedido.url(), dados)
        self.assertEqual(resposta.status_code, 302)
        aluno.refresh_from_db()
        self.assertEqual(aluno.endereco, dados['endereco'])
        self.assertFalse(DocumentoAtleta.objects.exists())

    def test_documentos_apenas_numeros_e_cpf_do_menor_separado_do_responsavel(self):
        convite = self.convite()
        dados = self.dados_familia(cpf='11144477735', rg='00123456')
        resposta = self.client.post(convite.url(), dados)
        self.assertEqual(resposta.status_code, 302)
        aluno = Atleta.objects.get()
        self.assertEqual(aluno.cpf, '11144477735')
        self.assertEqual(aluno.rg, '00123456')
        self.assertEqual(aluno.responsavel_financeiro.cpf, '52998224725')
        self.assertFalse(DocumentoAtleta.objects.exists())

    def test_unidade_filtra_grade_e_home_nao_exibe_precos(self):
        unidade = Unidade.objects.create(academia=self.a, nome='Outro polo')
        Turma.objects.create(academia=self.a, modalidade=self.mod, unidade=unidade, nome='Outra turma')
        resposta = self.client.get('/', {'unidade_turmas': self.unit.pk})
        self.assertContains(resposta, 'Infantil')
        self.assertNotContains(resposta, 'Outra turma')
        self.assertNotContains(resposta, 'R$130')
        self.assertNotContains(resposta, 'id="mensalidades"')
        unidade_alheia = Unidade.objects.create(academia=self.b, nome='Privada')
        self.assertEqual(self.client.get('/', {'unidade_turmas': unidade_alheia.pk}).status_code, 404)

    def test_avulsa_nao_duplica_nao_recorrente_e_aceita_baixa_manual(self):
        aluno = Atleta.objects.create(academia=self.a, nome='Compra')
        matricula = Matricula.objects.create(academia=self.a, atleta=aluno, modalidade=self.mod,
            valor_mensalidade=130, data_inicio=timezone.localdate())
        self.client.force_login(self.admin)
        url = '/financeiro/cobrancas/nova/'
        pagina = self.client.get(url)
        chave = pagina.context['form'].initial.get('chave') or pagina.context['form'].fields['chave'].initial
        dados = dict(chave=chave, matricula=matricula.pk, tipo='judogui', descricao='A2', valor='230.00', vencimento=timezone.localdate().isoformat())
        self.assertEqual(self.client.post(url, dados).status_code, 302)
        self.assertEqual(self.client.post(url, dados).status_code, 302)
        cobranca = Mensalidade.objects.get(tipo='judogui')
        self.assertEqual(cobranca.descricao, 'Judogui — A2')
        self.assertEqual(cobranca.valor, Decimal('230'))
        self.assertIsNone(cobranca.valor_apos_vencimento)
        gerar_mensalidades(2027, 1)
        self.assertEqual(Mensalidade.objects.filter(tipo='judogui').count(), 1)
        resposta = self.client.post(f'/financeiro/cobrancas/{cobranca.pk}/pagar/', {'forma_pagamento':'dinheiro'})
        self.assertEqual(resposta.status_code, 302)
        cobranca.refresh_from_db()
        self.assertEqual(cobranca.status, 'paga')
        self.assertEqual(cobranca.forma_pagamento, 'dinheiro')

    def test_cobranca_avulsa_isola_academias_e_so_admin_cria(self):
        self.client.force_login(self.prof)
        self.assertEqual(self.client.get('/financeiro/cobrancas/nova/').status_code, 403)
        self.client.force_login(self.admin)
        aluno = Atleta.objects.create(academia=self.b, nome='Outra')
        from modalidades.models import Modalidade
        modalidade = Modalidade.objects.create(academia=self.b, nome='Judô')
        matricula = Matricula.objects.create(academia=self.b, atleta=aluno, modalidade=modalidade,
            valor_mensalidade=130, data_inicio=timezone.localdate())
        pagina = self.client.get('/financeiro/cobrancas/nova/')
        chave = pagina.context['form'].fields['chave'].initial
        resposta = self.client.post('/financeiro/cobrancas/nova/', dict(chave=chave,matricula=matricula.pk,
            tipo='compra_avulsa',valor='10',vencimento=timezone.localdate().isoformat()))
        self.assertIn('matricula', resposta.context['form'].errors)
        self.assertFalse(Mensalidade.objects.exists())


class PixAvulsoTests(CenarioWoovi, TestCase):
    def setUp(self):
        self.criar_cenario()
        self.usar_conta_propria()

    @patch('integracoes.woovi.services.WooviClient')
    def test_pix_dos_tres_tipos_usa_fluxo_existente_sem_duplicar(self, cliente):
        from integracoes.woovi.services import garantir_cobranca_pix
        from integracoes.whatsapp.evolution import contexto_cobranca
        cliente.return_value.criar_cobranca.side_effect = cobranca_criada
        for tipo in Mensalidade.TIPOS_AVULSOS:
            cobranca = Mensalidade.objects.create(academia=self.academia,matricula=self.matricula,
                competencia=timezone.localdate().replace(day=1),vencimento=timezone.localdate(),
                valor=Decimal('90'),tipo=tipo,descricao_avulsa='Item')
            pix = garantir_cobranca_pix(cobranca)
            self.assertEqual(garantir_cobranca_pix(cobranca).pk, pix.pk)
            self.assertEqual(pix.valor, Decimal('90'))
            self.assertIn(cobranca.get_tipo_display().lower(), contexto_cobranca(cobranca))
        self.assertEqual(cliente.return_value.criar_cobranca.call_count, 3)

    @override_settings(EVOLUTION_BOTOES=False)
    @patch('integracoes.whatsapp.evolution.EvolutionWhatsAppProvider._enviar_texto', return_value={})
    def test_envia_fatura_direto_pelo_evolution(self, envio):
        from academias.models import IntegracaoWhatsApp
        from .models import AcessoAcademia
        IntegracaoWhatsApp.objects.create(academia=self.academia,provider=IntegracaoWhatsApp.PROVIDER_EVOLUTION)
        AcessoAcademia.objects.create(usuario=self.usuario,academia=self.academia,administrador=True)
        self.client.force_login(self.usuario)
        for tipo in Mensalidade.TIPOS_AVULSOS:
            cobranca = Mensalidade.objects.create(academia=self.academia,matricula=self.matricula,
                competencia=timezone.localdate().replace(day=1),vencimento=timezone.localdate(),valor=90,tipo=tipo)
            resposta = self.client.post(f'/financeiro/cobrancas/{cobranca.pk}/enviar/')
            self.assertEqual(resposta.status_code, 302)
            self.assertNotIn('wa.me', resposta['Location'])
            self.assertIn(cobranca.get_tipo_display().lower(), envio.call_args.args[1])
        self.assertEqual(envio.call_count, 3)
