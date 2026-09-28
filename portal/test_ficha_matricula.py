import io
import zipfile
from datetime import date, datetime, timedelta
from decimal import Decimal
from xml.sax.saxutils import escape

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone

from academias.models import Academia, Unidade
from atletas.models import Atleta, Responsavel
from financeiro.models import Mensalidade
from matriculas.models import Matricula
from modalidades.models import Modalidade, Turma
from portal.importacao_fichas import PlanilhaInvalida, importar_planilha
from portal.models import AcessoAcademia, ConviteMatricula, FichaMatricula
from portal.models_matricula import PERGUNTAS_SAUDE

TERMOS = '1. A Escola de Judô Keiko Fukuda (EJKF) se obriga a ministrar aulas de judô.\n\nSejam Bem Vindos!'
DECLARACAO = '1. DECLARO QUE OS DADOS FORNECIDOS NESTE CADASTRAMENTO SÃO VERDADEIROS.'


class Cenario(TestCase):
    def setUp(self):
        User = get_user_model()
        self.academia = Academia.objects.create(nome='Keiko', cnpj='K1')
        self.admin = User.objects.create_user('dona', password='x')
        AcessoAcademia.objects.create(usuario=self.admin, academia=self.academia, administrador=True)
        self.prof = User.objects.create_user('prof', password='x')
        AcessoAcademia.objects.create(usuario=self.prof, academia=self.academia, administrador=False)
        self.unidade = Unidade.objects.create(academia=self.academia, nome='Matriz')
        self.modalidade = Modalidade.objects.create(academia=self.academia, nome='Judô')
        self.turma = Turma.objects.create(
            academia=self.academia, modalidade=self.modalidade, unidade=self.unidade, nome='Kids',
            valor_mensalidade=Decimal('150.00'), valor_apos_vencimento=Decimal('170.00'), dia_vencimento=10,
        )

    def convite(self, **kw):
        return ConviteMatricula.gerar(
            academia=self.academia, modalidade=self.modalidade, turma=self.turma,
            unidade=self.unidade, criado_por=self.prof, **kw,
        )

    def ficha(self, **kw):
        dados = dict(
            nome='Ana  Souza', data_nascimento='2016-03-02', responsavel_nome='Maria Souza',
            telefone='(21) 98888-7777', email='maria@teste.com', autorizados_buscar='Maria (mãe)',
            vencimento_preferido='15',
        )
        dados.update({campo: 'nao' for campo, *_ in PERGUNTAS_SAUDE})
        dados.update(kw)
        return dados


class FichaNoConviteTests(Cenario):
    def test_termos_da_academia_exigem_o_aceite(self):
        self.academia.termos_matricula = TERMOS
        self.academia.declaracao_matricula = DECLARACAO
        self.academia.save()
        c = self.convite()

        pagina = self.client.get(f'/matricula/{c.token}/')
        self.assertContains(pagina, 'se obriga a ministrar')
        self.assertContains(pagina, 'Estou ciente e concordo em prosseguir com a matrícula.')

        resp = self.client.post(f'/matricula/{c.token}/', self.ficha())
        self.assertEqual(Atleta.objects.count(), 0)
        self.assertIn('aceite_termos', resp.context['form'].errors)
        self.assertIn('aceite_declaracao', resp.context['form'].errors)

        self.client.post(f'/matricula/{c.token}/', self.ficha(aceite_termos='on', aceite_declaracao='on'))
        ficha = FichaMatricula.objects.get()
        self.assertTrue(ficha.aceitou_termos and ficha.aceitou_declaracao)
        # Guarda o texto aceito: se a escola mudar os termos, fica o registro.
        self.assertEqual((ficha.termos_aceitos, ficha.declaracao_aceita), (TERMOS, DECLARACAO))

    def test_sem_termos_cadastrados_nao_pede_aceite(self):
        c = self.convite()
        self.assertNotContains(self.client.get(f'/matricula/{c.token}/'), 'Estou ciente')
        self.client.post(f'/matricula/{c.token}/', self.ficha())
        self.assertEqual(Atleta.objects.count(), 1)

    def test_guarda_as_respostas_e_usa_o_vencimento_escolhido(self):
        c = self.convite()
        self.client.post(f'/matricula/{c.token}/', self.ficha(
            saude_medicacao='sim', saude_medicacao_qual='Bombinha', saude_cirurgia='sim',
        ))
        aluno = Atleta.objects.get()
        self.assertEqual((aluno.nome, aluno.data_nascimento), ('Ana Souza', date(2016, 3, 2)))
        matricula = Matricula.objects.get()
        self.assertEqual(matricula.dia_vencimento, 15)
        self.assertEqual(matricula.valor_apos_vencimento, Decimal('170.00'))

        ficha = FichaMatricula.objects.get()
        self.assertEqual((ficha.origem, ficha.convite, ficha.atleta), (FichaMatricula.CONVITE, c, aluno))
        self.assertEqual(ficha.autorizados_buscar, 'Maria (mãe)')
        self.assertEqual((ficha.vencimento_preferido, ficha.data_nascimento_informada), (15, '02/03/2016'))
        self.assertTrue(ficha.saude_medicacao)
        self.assertEqual(ficha.saude_medicacao_qual, 'Bombinha')
        self.assertFalse(ficha.saude_tontura)
        self.assertEqual(len(ficha.alertas_saude), 2)

    def test_dia_fixado_no_convite_vale_sobre_a_escolha_da_familia(self):
        c = self.convite(dia_vencimento=5)
        self.client.post(f'/matricula/{c.token}/', self.ficha(vencimento_preferido='15'))
        self.assertEqual(Matricula.objects.get().dia_vencimento, 5)

    def test_whatsapp_sem_ddd_e_recusado(self):
        c = self.convite()
        resp = self.client.post(f'/matricula/{c.token}/', self.ficha(telefone='98888-7777'))
        self.assertIn('telefone', resp.context['form'].errors)
        self.assertEqual(Atleta.objects.count(), 0)

    def test_ficha_aparece_na_revisao_e_no_cadastro_do_aluno(self):
        c = self.convite()
        self.client.post(f'/matricula/{c.token}/', self.ficha(saude_tontura='sim'))
        aluno = Atleta.objects.get()
        self.client.force_login(self.admin)

        revisao = self.client.get(f'/matriculas/convites/{c.pk}/')
        self.assertContains(revisao, 'Ficha de matrícula')
        self.assertContains(revisao, 'Atenção à saúde')

        cadastro = self.client.get(f'/alunos/{aluno.pk}/')
        self.assertContains(cadastro, 'Ficha de matrícula')
        self.assertContains(cadastro, 'Maria (mãe)')
        self.assertContains(cadastro, 'tontura')


# ------------------------------------------------------------ importação
CABECALHO = [
    'Carimbo de data/hora', TERMOS, 'Nome completo do (a) aluno (a):', 'Data de nascimento do (a) aluno (a)',
    'Telefone para contato', 'E-mail para contato', 'Quem está autorizado (a) a buscar o (a) aluno (a)?',
    'Qual é a melhor data para o vencimento da mensalidade?',
] + [texto for _c, pergunta, _cd, detalhe in PERGUNTAS_SAUDE for texto in ((pergunta + ' '), detalhe) if texto] + [
    DECLARACAO,
]


def resposta(nome, *, quando=45000.5, nascimento='42000', telefone='(21) 98888-7777', email='mae@x.com',
             vencimento='Até o dia 05 de cada mês', sim=()):
    linha = [str(quando), 'Estou ciente e concordo em prosseguir com a matrícula.', nome, nascimento,
             telefone, email, 'Mãe', vencimento]
    for campo, _pergunta, campo_detalhe, _detalhe in PERGUNTAS_SAUDE:
        linha.append('Sim' if campo in sim else 'Não')
        if campo_detalhe:
            linha.append('Remédio X' if campo in sim else '')
    linha.append('Estou ciente e concordo.')
    return linha


def planilha(*linhas):
    """Um .xlsx mínimo, como o Google Forms exporta: textos em
    sharedStrings e números (datas, telefones) como valor."""
    textos, indice = [], {}
    xml_linhas = []
    for r, linha in enumerate([CABECALHO, *linhas], start=1):
        celulas = []
        for c, valor in enumerate(linha):
            ref = f'{chr(65 + c % 26) if c < 26 else chr(64 + c // 26) + chr(65 + c % 26)}{r}'
            if valor == '':
                continue
            try:
                float(valor)
                celulas.append(f'<c r="{ref}"><v>{valor}</v></c>')
            except ValueError:
                if valor not in indice:
                    indice[valor] = len(textos)
                    textos.append(valor)
                celulas.append(f'<c r="{ref}" t="s"><v>{indice[valor]}</v></c>')
        xml_linhas.append(f'<row r="{r}">{"".join(celulas)}</row>')
    ns = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as z:
        z.writestr('xl/worksheets/sheet1.xml', f'<worksheet xmlns="{ns}"><sheetData>{"".join(xml_linhas)}</sheetData></worksheet>')
        z.writestr('xl/sharedStrings.xml', f'<sst xmlns="{ns}">' + ''.join(
            f'<si><t xml:space="preserve">{escape(t)}</t></si>' for t in textos) + '</sst>')
    buffer.seek(0)
    return buffer


class ImportacaoTests(Cenario):
    def importar(self, arquivo, gravar=True, a_partir_de=None):
        return importar_planilha(
            self.academia, arquivo, turma=self.turma,
            cobrar_a_partir_de=a_partir_de or timezone.localdate(), gravar=gravar,
        )

    def test_conferencia_nao_grava_nada(self):
        relatorio = self.importar(planilha(resposta('Bia Lima')), gravar=False)
        self.assertEqual(relatorio.novos, 1)
        self.assertFalse(Atleta.objects.exists() or FichaMatricula.objects.exists())
        self.academia.refresh_from_db()
        self.assertEqual(self.academia.termos_matricula, '')

    def test_aluno_novo_entra_com_matricula_ativa_e_cobranca_no_proximo_vencimento(self):
        hoje = timezone.localdate()
        relatorio = self.importar(planilha(resposta('Bia Lima', nascimento='42000')))
        self.assertEqual(relatorio.novos, 1)

        aluno = Atleta.objects.get()
        self.assertEqual((aluno.nome, aluno.status), ('Bia Lima', 'ativo'))
        self.assertEqual(aluno.data_nascimento, date(2014, 12, 27))  # 42000 no calendário do Excel
        responsavel = aluno.responsavel_financeiro
        self.assertEqual((responsavel.nome, responsavel.whatsapp), ('Responsável de Bia Lima', '21988887777'))

        matricula = Matricula.objects.get()
        self.assertTrue(matricula.ativo)
        self.assertEqual((matricula.turma, matricula.dia_vencimento), (self.turma, 5))
        self.assertEqual(matricula.valor_apos_vencimento, Decimal('170.00'))
        self.assertIsNone(matricula.taxa_matricula)
        mensalidade = Mensalidade.objects.get()  # nenhuma taxa de matrícula
        self.assertEqual(mensalidade.tipo, Mensalidade.MENSALIDADE)
        self.assertGreaterEqual(mensalidade.vencimento, hoje)
        self.assertEqual(mensalidade.vencimento.day, 5)

        ficha = FichaMatricula.objects.get()
        self.assertEqual((ficha.origem, ficha.atleta, ficha.vencimento_preferido), (FichaMatricula.PLANILHA, aluno, 5))
        self.assertEqual(ficha.respondida_em, timezone.make_aware(datetime(2023, 3, 15, 12, 0)))
        self.assertTrue(ficha.aceitou_termos)
        self.assertEqual(ficha.termos_aceitos, TERMOS)

    def test_aluno_existente_so_ganha_a_ficha(self):
        responsavel = Responsavel.objects.create(academia=self.academia, nome='Pai', whatsapp='21911112222')
        existente = Atleta.objects.create(
            academia=self.academia, nome='João Émerson', data_nascimento=date(2015, 1, 1),
            responsavel_financeiro=responsavel,
        )
        relatorio = self.importar(planilha(resposta('joao  emerson', nascimento='42000', telefone='21933334444')))
        self.assertEqual((relatorio.novos, relatorio.existentes), (0, 1))
        existente.refresh_from_db()
        self.assertEqual((existente.nome, existente.data_nascimento), ('João Émerson', date(2015, 1, 1)))
        self.assertEqual(existente.responsavel_financeiro.whatsapp, '21911112222')
        self.assertFalse(Matricula.objects.exists())
        self.assertEqual(FichaMatricula.objects.get().atleta, existente)

    def test_importar_de_novo_nao_duplica(self):
        arquivo = planilha(resposta('Bia Lima'))
        self.importar(arquivo)
        arquivo.seek(0)
        relatorio = self.importar(arquivo)
        self.assertEqual((relatorio.novos, relatorio.existentes, relatorio.ignoradas), (0, 0, 1))
        self.assertEqual((Atleta.objects.count(), FichaMatricula.objects.count(), Matricula.objects.count()), (1, 1, 1))

    def test_irmaos_com_o_mesmo_telefone_tem_o_mesmo_responsavel(self):
        self.importar(planilha(resposta('Bia Lima', quando=45000.1), resposta('Caio Lima', quando=45000.2)))
        self.assertEqual(Responsavel.objects.count(), 1)
        self.assertEqual(Atleta.objects.filter(responsavel_financeiro=Responsavel.objects.get()).count(), 2)

    def test_telefone_em_notacao_cientifica_e_data_impossivel(self):
        relatorio = self.importar(planilha(resposta('Bia Lima', telefone='2.1988887777E10', nascimento='10/1/0013')))
        aluno = Atleta.objects.get()
        self.assertEqual(aluno.responsavel_financeiro.whatsapp, '21988887777')
        self.assertIsNone(aluno.data_nascimento)
        ficha = FichaMatricula.objects.get()
        self.assertEqual(ficha.data_nascimento_informada, '10/1/0013')
        self.assertTrue(any('nascimento' in aviso for aviso in relatorio.linhas[0].avisos))

    def test_telefone_sem_ddd_avisa(self):
        relatorio = self.importar(planilha(resposta('Bia Lima', telefone='98888-7777')))
        self.assertEqual(Atleta.objects.get().responsavel_financeiro.whatsapp, '')
        self.assertTrue(any('WhatsApp' in aviso for aviso in relatorio.linhas[0].avisos))

    def test_respostas_de_saude_e_termos_copiados_para_a_academia(self):
        relatorio = self.importar(planilha(resposta('Bia Lima', sim=('saude_medicacao',))))
        ficha = FichaMatricula.objects.get()
        self.assertTrue(ficha.saude_medicacao)
        self.assertEqual(ficha.saude_medicacao_qual, 'Remédio X')
        self.assertFalse(ficha.saude_coracao)
        self.assertTrue(relatorio.termos_copiados)
        self.academia.refresh_from_db()
        self.assertEqual((self.academia.termos_matricula, self.academia.declaracao_matricula), (TERMOS, DECLARACAO))

    def test_arquivo_que_nao_e_a_planilha(self):
        with self.assertRaises(PlanilhaInvalida):
            self.importar(io.BytesIO(b'nao e zip'))

    def test_tela_so_para_administrador(self):
        self.client.force_login(self.prof)
        self.assertEqual(self.client.get('/matriculas/importar/').status_code, 403)

    def test_tela_confere_e_importa(self):
        self.client.force_login(self.admin)
        hoje = timezone.localdate().isoformat()

        def enviar(acao):
            arquivo = SimpleUploadedFile('respostas.xlsx', planilha(resposta('Bia Lima')).read())
            return self.client.post('/matriculas/importar/', {
                'arquivo': arquivo, 'turma': self.turma.pk, 'cobrar_a_partir_de': hoje, 'acao': acao,
            })

        conferencia = enviar('conferir')
        self.assertContains(conferencia, 'nada foi gravado')
        self.assertContains(conferencia, 'Cadastrar aluno e matrícula')
        self.assertFalse(Atleta.objects.exists())

        importacao = enviar('importar')
        self.assertContains(importacao, 'Importação concluída')
        self.assertEqual(Atleta.objects.count(), 1)

    def test_sem_turma_com_modalidade_e_valores_informados(self):
        from portal.importacao_fichas import CondicoesMatricula

        condicoes = CondicoesMatricula(
            modalidade=self.modalidade, valor_mensalidade=Decimal('120.00'),
            valor_apos_vencimento=Decimal('130.00'), dia_vencimento=10,
        )
        importar_planilha(
            self.academia,
            planilha(resposta('Bia Lima', quando=45000.1),
                     resposta('Caio Lima', quando=45000.2, telefone='21977776666', vencimento='')),
            condicoes=condicoes, cobrar_a_partir_de=timezone.localdate(), gravar=True,
        )
        bia, caio = Matricula.objects.order_by('atleta__nome')
        for matricula in (bia, caio):
            self.assertIsNone(matricula.turma)
            self.assertTrue(matricula.ativo)
            self.assertEqual(matricula.modalidade, self.modalidade)
            self.assertEqual((matricula.valor_mensalidade, matricula.valor_apos_vencimento), (Decimal('120.00'), Decimal('130.00')))
        # Quem escolheu na ficha mantém; quem não escolheu, o dia informado.
        self.assertEqual((bia.dia_vencimento, caio.dia_vencimento), (5, 10))
        mensalidade = Mensalidade.objects.filter(matricula=bia).get()
        self.assertEqual((mensalidade.valor, mensalidade.valor_apos_vencimento), (Decimal('120.00'), Decimal('130.00')))

    def test_tela_sem_turma_exige_modalidade_e_valor(self):
        self.client.force_login(self.admin)
        hoje = timezone.localdate().isoformat()

        def enviar(**campos):
            arquivo = SimpleUploadedFile('respostas.xlsx', planilha(resposta('Bia Lima')).read())
            return self.client.post('/matriculas/importar/', {
                'arquivo': arquivo, 'turma': '', 'cobrar_a_partir_de': hoje, 'acao': 'importar', **campos,
            })

        resp = enviar()
        self.assertIn('modalidade', resp.context['form'].errors)
        self.assertIn('valor_mensalidade', resp.context['form'].errors)
        self.assertFalse(Atleta.objects.exists())

        enviar(modalidade=self.modalidade.pk, valor_mensalidade='120', valor_apos_vencimento='130', dia_vencimento='10')
        matricula = Matricula.objects.get()
        self.assertIsNone(matricula.turma)
        self.assertEqual(matricula.valor_apos_vencimento, Decimal('130.00'))

    def test_tela_recusa_data_no_passado(self):
        self.client.force_login(self.admin)
        arquivo = SimpleUploadedFile('respostas.xlsx', planilha(resposta('Bia Lima')).read())
        resp = self.client.post('/matriculas/importar/', {
            'arquivo': arquivo, 'turma': self.turma.pk, 'acao': 'importar',
            'cobrar_a_partir_de': (timezone.localdate() - timedelta(days=1)).isoformat(),
        })
        self.assertIn('cobrar_a_partir_de', resp.context['form'].errors)
        self.assertFalse(Atleta.objects.exists())
