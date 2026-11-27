import io
import shutil
import tempfile
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from PIL import Image

from academias.models import Academia, Unidade
from atletas.models import Atleta, DocumentoAtleta, Responsavel
from integracoes.whatsapp.base import WhatsAppProviderError
from matriculas.models import Matricula
from modalidades.models import Modalidade, Turma
from portal.models import (
    AcessoAcademia, CampanhaRecadastramento, FichaMatricula, PedidoRecadastramento,
)
from portal.models_matricula import PERGUNTAS_SAUDE
from portal.services_recadastramento import (
    abrir_campanha, alunos_do_publico, enviar_pendentes, rotina_diaria,
)

ENVIO = 'integracoes.whatsapp.enviar_recadastramento'


def imagem(formato='PNG', tamanho=(1200, 900), nome='foto.png'):
    buffer = io.BytesIO()
    Image.new('RGB', tamanho, (200, 30, 40)).save(buffer, formato)
    return SimpleUploadedFile(nome, buffer.getvalue(), content_type=f'image/{formato.lower()}')


def pdf(nome='atestado.pdf'):
    return SimpleUploadedFile(nome, b'%PDF-1.4\n%fake\n', content_type='application/pdf')


class BaseRecadastramento(TestCase):
    def setUp(self):
        self.media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)
        ajustes = override_settings(
            MEDIA_ROOT=self.media, RECADASTRAMENTO_EM_SEGUNDO_PLANO=False,
            SITE_URL='https://escola.example.com',
        )
        ajustes.enable()
        self.addCleanup(ajustes.disable)

        User = get_user_model()
        self.a = Academia.objects.create(nome='Keiko', cnpj='1', termos_matricula='Regras', declaracao_matricula='Declaro')
        self.b = Academia.objects.create(nome='Outra', cnpj='2')
        self.dona = User.objects.create_user('dona', password='x')
        AcessoAcademia.objects.create(usuario=self.dona, academia=self.a, administrador=True)
        self.prof = User.objects.create_user('prof', password='x')
        AcessoAcademia.objects.create(usuario=self.prof, academia=self.a, administrador=False)
        self.intruso = User.objects.create_user('intruso', password='x')
        AcessoAcademia.objects.create(usuario=self.intruso, academia=self.b, administrador=True)

        self.mae = Responsavel.objects.create(academia=self.a, nome='Maria Souza', whatsapp='21999990000', cpf='52998224725')
        self.ana = Atleta.objects.create(academia=self.a, nome='Ana Souza', responsavel_financeiro=self.mae)
        self.beto = Atleta.objects.create(academia=self.a, nome='Beto Souza', responsavel_financeiro=self.mae)
        self.caio = Atleta.objects.create(academia=self.a, nome='Caio Sem Fone')
        Atleta.objects.create(academia=self.a, nome='Inativo', status='inativo', telefone='21988887777')
        Atleta.objects.create(academia=self.b, nome='De outra escola', telefone='21977776666')

    def campanha(self, **kw):
        base = dict(titulo='Recadastramento 2027', prazo=timezone.localdate() + timedelta(days=30),
                    alunos=alunos_do_publico(self.a), documentos=[DocumentoAtleta.ATESTADO])
        base.update(kw)
        return abrir_campanha(self.a, **base)

    def resposta(self, **kw):
        base = dict(
            aceite_termos='on', aceite_declaracao='on', nome='Ana Souza Lima', data_nascimento='2016-03-02',
            responsavel_nome='Maria Souza', responsavel_cpf='529.982.247-25', telefone='(21) 98888-1111',
            email='maria@exemplo.com', autorizados_buscar='Maria e avó',
        )
        base.update({campo: 'nao' for campo, *_ in PERGUNTAS_SAUDE})
        base['saude_medicacao'] = 'sim'
        base['saude_medicacao_qual'] = 'Bombinha'
        base.update(kw)
        return base


class AberturaNoPainel(BaseRecadastramento):
    def test_admin_abre_para_os_alunos_ativos_da_academia(self):
        self.client.force_login(self.dona)
        with patch(ENVIO) as envio:
            resp = self.client.post('/alunos/recadastramento/', {
                'titulo': 'Recadastramento 2027', 'prazo': (timezone.localdate() + timedelta(days=20)).isoformat(),
                'pedir_foto': 'on', 'documentos': ['atestado', 'identidade'], 'enviar_agora': 'on',
            })
        campanha = CampanhaRecadastramento.objects.get()
        self.assertRedirects(resp, f'/alunos/recadastramento/{campanha.pk}/')
        self.assertEqual(
            sorted(campanha.pedidos.values_list('atleta__nome', flat=True)),
            ['Ana Souza', 'Beto Souza', 'Caio Sem Fone'],
        )
        self.assertEqual(campanha.documentos_pedidos, 'atestado,identidade')
        # Irmãos no mesmo WhatsApp: uma mensagem só, com os dois links.
        envio.assert_called_once()
        _academia, telefone, texto = envio.call_args.args
        self.assertEqual(telefone, '21999990000')
        self.assertIn('Ana Souza', texto)
        self.assertIn('Beto Souza', texto)
        self.assertIn('https://escola.example.com/recadastramento/', texto)
        self.assertEqual(campanha.pedidos.get(atleta=self.caio).status, PedidoRecadastramento.SEM_CONTATO)
        self.assertEqual(campanha.pedidos.filter(status=PedidoRecadastramento.ENVIADO).count(), 2)

    def test_professor_nao_abre(self):
        self.client.force_login(self.prof)
        resp = self.client.post('/alunos/recadastramento/', {
            'titulo': 'X', 'prazo': (timezone.localdate() + timedelta(days=5)).isoformat(),
        })
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(CampanhaRecadastramento.objects.exists())
        self.assertEqual(self.client.get('/alunos/recadastramento/').status_code, 200)

    def test_so_uma_turma(self):
        mod = Modalidade.objects.create(academia=self.a, nome='Judô')
        turma = Turma.objects.create(academia=self.a, modalidade=mod, nome='Infantil', valor_mensalidade=Decimal('100'))
        Matricula.objects.create(academia=self.a, atleta=self.beto, modalidade=mod, turma=turma,
                                 valor_mensalidade=Decimal('100'), dia_vencimento=10, data_inicio=date(2026, 1, 1))
        campanha = self.campanha(alunos=alunos_do_publico(self.a, turma))
        self.assertEqual(list(campanha.pedidos.values_list('atleta__nome', flat=True)), ['Beto Souza'])

    def test_detalhe_isolado_por_academia(self):
        campanha = self.campanha()
        self.client.force_login(self.intruso)
        self.assertEqual(self.client.get(f'/alunos/recadastramento/{campanha.pk}/').status_code, 404)
        self.client.force_login(self.dona)
        resp = self.client.get(f'/alunos/recadastramento/{campanha.pk}/')
        self.assertContains(resp, 'Caio Sem Fone')
        self.assertContains(resp, 'Copiar link')


class Envio(BaseRecadastramento):
    def test_falha_volta_para_a_fila_e_registra_o_erro(self):
        campanha = self.campanha()
        with patch(ENVIO, side_effect=WhatsAppProviderError('WhatsApp desconectado')):
            resultado = enviar_pendentes(campanha, pausa=0)
        self.assertEqual(resultado, {'enviados': 0, 'falhas': 1})
        pedido = campanha.pedidos.get(atleta=self.ana)
        self.assertEqual((pedido.status, pedido.tentativas_envio), (PedidoRecadastramento.PENDENTE, 1))
        self.assertIn('desconectado', pedido.ultimo_erro)

    def test_nao_reenvia_o_que_ja_saiu(self):
        campanha = self.campanha()
        with patch(ENVIO) as envio:
            enviar_pendentes(campanha, pausa=0)
            enviar_pendentes(campanha, pausa=0)
        self.assertEqual(envio.call_count, 1)

    def test_lembrete_para_quem_nao_respondeu(self):
        campanha = self.campanha()
        with patch(ENVIO):
            enviar_pendentes(campanha, pausa=0)
        self.client.force_login(self.dona)
        with patch(ENVIO) as envio:
            self.client.post(f'/alunos/recadastramento/{campanha.pk}/acao/', {'acao': 'lembrar'})
        envio.assert_called_once()
        self.assertIn('Lembrete', envio.call_args.args[2])

    def test_encerrado_nao_envia_e_link_fecha(self):
        campanha = self.campanha()
        self.client.force_login(self.dona)
        self.client.post(f'/alunos/recadastramento/{campanha.pk}/acao/', {'acao': 'encerrar'})
        with patch(ENVIO) as envio:
            enviar_pendentes(campanha, pausa=0)
        envio.assert_not_called()
        pedido = campanha.pedidos.get(atleta=self.ana)
        self.client.logout()
        self.assertContains(self.client.get(pedido.url()), 'Recadastramento encerrado')


class RespostaDaFamilia(BaseRecadastramento):
    def test_trocar_para_proprio_responsavel_preserva_responsavel_da_familia(self):
        campanha = self.campanha(pedir_foto=False, documentos=[])
        pedido = campanha.pedidos.get(atleta=self.ana)
        dados = self.resposta(proprio_responsavel='on', cpf='11144477735')
        dados.pop('responsavel_nome')
        dados.pop('responsavel_cpf')
        resp = self.client.post(pedido.url(), dados)
        self.assertEqual(resp.status_code, 302)
        self.ana.refresh_from_db()
        self.mae.refresh_from_db()
        self.assertTrue(self.ana.proprio_responsavel)
        self.assertNotEqual(self.ana.responsavel_financeiro_id, self.mae.pk)
        self.assertEqual(self.mae.nome, 'Maria Souza')
        self.assertEqual(self.mae.whatsapp, '21999990000')

    def test_recadastramento_preenche_e_atualiza_federacao(self):
        from .services_recadastramento import dados_iniciais
        self.ana.federado = True
        self.ana.numero_zempo = '000123'
        self.ana.numero_fjerj = '001-25'
        self.ana.save()
        campanha = self.campanha(pedir_foto=False, documentos=[])
        pedido = campanha.pedidos.get(atleta=self.ana)
        self.assertEqual(dados_iniciais(pedido)['federado'], 'sim')
        self.assertEqual(dados_iniciais(pedido)['numero_zempo'], '000123')
        pagina = self.client.get(pedido.url())
        self.assertContains(pagina, 'Número FJERJ')
        resposta = self.client.post(pedido.url(), self.resposta(
            federado='nao', numero_zempo='', numero_fjerj='0099'))
        self.assertEqual(resposta.status_code, 302)
        self.ana.refresh_from_db()
        self.assertIs(self.ana.federado, False)
        self.assertEqual(self.ana.numero_zempo, '')
        self.assertEqual(self.ana.numero_fjerj, '0099')

    def setUp(self):
        super().setUp()
        FichaMatricula.objects.create(
            academia=self.a, atleta=self.ana, origem=FichaMatricula.PLANILHA, respondida_em=timezone.now(),
            nome_aluno='Ana Souza', autorizados_buscar='Só a mãe', vencimento_preferido=15,
            email_contato='antigo@exemplo.com', saude_coracao=False,
        )
        self.camp = self.campanha()
        self.pedido = self.camp.pedidos.get(atleta=self.ana)

    def test_formulario_vem_preenchido(self):
        resp = self.client.get(self.pedido.url())
        self.assertContains(resp, 'value="Ana Souza"')
        self.assertContains(resp, 'Só a mãe')
        self.assertContains(resp, 'antigo@exemplo.com')
        self.assertContains(resp, 'enctype="multipart/form-data"')
        self.assertNotContains(resp, 'vencimento_preferido')

    def test_resposta_atualiza_cadastro_e_guarda_foto_e_documento(self):
        resp = self.client.post(self.pedido.url(), {
            **self.resposta(), 'foto': imagem(), 'documento_atestado': pdf(),
        })
        self.assertRedirects(resp, '/recadastramento/recebido/', fetch_redirect_response=False)
        self.ana.refresh_from_db()
        self.mae.refresh_from_db()
        self.assertEqual(self.ana.nome, 'Ana Souza Lima')
        self.assertEqual(self.ana.data_nascimento, date(2016, 3, 2))
        self.assertEqual((self.mae.whatsapp, self.mae.email), ('21988881111', 'maria@exemplo.com'))

        ficha = self.ana.fichas_matricula.first()
        self.assertEqual(ficha.origem, FichaMatricula.RECADASTRAMENTO)
        self.assertTrue(ficha.saude_medicacao)
        self.assertEqual(ficha.saude_medicacao_qual, 'Bombinha')
        self.assertEqual(ficha.termos_aceitos, 'Regras')
        self.assertEqual(ficha.vencimento_preferido, 15)  # mantém o da ficha anterior

        self.assertTrue(self.ana.foto)
        with Image.open(self.ana.foto.path) as foto:
            self.assertEqual(foto.format, 'JPEG')
            self.assertLessEqual(max(foto.size), 800)
        documento = DocumentoAtleta.objects.get(atleta=self.ana)
        self.assertEqual((documento.tipo, documento.enviado_pela_familia), ('atestado', True))

        self.pedido.refresh_from_db()
        self.assertEqual((self.pedido.status, self.pedido.ficha), (PedidoRecadastramento.RESPONDIDO, ficha))
        self.assertContains(self.client.get(self.pedido.url()), 'já foi respondido')

    def test_foto_obrigatoria_quando_o_aluno_nao_tem(self):
        resp = self.client.post(self.pedido.url(), self.resposta())
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.ana.fichas_matricula.count(), 1)

    def test_documento_falso_e_recusado(self):
        resp = self.client.post(self.pedido.url(), {
            **self.resposta(), 'foto': imagem(),
            'documento_atestado': SimpleUploadedFile('atestado.pdf', b'MZ executavel'),
        })
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'não parece ser do tipo')
        self.assertFalse(DocumentoAtleta.objects.exists())

    def test_link_invalido(self):
        self.assertEqual(self.client.get('/recadastramento/nao-existe/').status_code, 404)


class RotinaAnual(BaseRecadastramento):
    def test_abre_no_mes_escolhido_uma_vez_por_ano(self):
        self.a.recadastramento_mes = 2
        self.a.save()
        with patch(ENVIO) as envio:
            self.assertEqual(rotina_diaria(date(2027, 1, 31))['abertas'], 0)
            self.assertEqual(rotina_diaria(date(2027, 2, 1))['abertas'], 1)
            self.assertEqual(rotina_diaria(date(2027, 2, 2))['abertas'], 0)
        campanha = CampanhaRecadastramento.objects.get()
        self.assertTrue(campanha.automatica)
        self.assertEqual(campanha.titulo, 'Recadastramento 2027')
        self.assertTrue(envio.called)

    def test_nao_abre_se_a_professora_abriu_ha_pouco(self):
        self.campanha()
        hoje = timezone.localdate()
        self.a.recadastramento_mes = hoje.month
        self.a.save()
        self.assertEqual(rotina_diaria(hoje)['abertas'], 0)


class FotoEDocumentosNoPainel(BaseRecadastramento):
    def test_foto_e_documento_no_perfil(self):
        self.client.force_login(self.prof)
        self.client.post(f'/alunos/{self.ana.pk}/foto/enviar/', {'foto': imagem('JPEG', nome='rosto.jpg')})
        self.ana.refresh_from_db()
        self.assertTrue(self.ana.foto)
        foto = self.client.get(f'/alunos/{self.ana.pk}/foto/')
        self.assertEqual(foto['Content-Type'], 'image/jpeg')
        self.assertEqual(foto['Cache-Control'], 'private, no-store')
        foto.close()

        self.client.post(f'/alunos/{self.ana.pk}/documentos/', {
            'tipo': 'atestado', 'arquivo': pdf(), 'validade': '2020-01-01',
        })
        documento = DocumentoAtleta.objects.get()
        self.assertEqual((documento.enviado_por, documento.nome_original), (self.prof, 'atestado.pdf'))
        resp = self.client.get(f'/alunos/{self.ana.pk}/')
        self.assertContains(resp, 'Atestado médico')
        self.assertContains(resp, 'Venceu em')
        self.assertContains(resp, f'/alunos/{self.ana.pk}/foto/')
        arquivo = self.client.get(f'/alunos/{self.ana.pk}/documentos/{documento.pk}/')
        self.assertEqual(arquivo['Content-Type'], 'application/pdf')
        self.assertEqual(arquivo['X-Content-Type-Options'], 'nosniff')
        self.assertIn('inline', arquivo['Content-Disposition'])
        arquivo.close()  # no Windows, arquivo aberto não pode ser apagado

        caminho = documento.arquivo.path
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(f'/alunos/{self.ana.pk}/documentos/{documento.pk}/excluir/')
        self.assertFalse(DocumentoAtleta.objects.exists())
        import os
        self.assertFalse(os.path.exists(caminho))

    def test_arquivos_so_para_a_propria_academia(self):
        self.ana.trocar_foto(__import__('atletas.arquivos', fromlist=['x']).preparar_foto(imagem()))
        documento = DocumentoAtleta.objects.create(academia=self.a, atleta=self.ana, tipo='cpf', arquivo=pdf())
        self.assertEqual(self.client.get(f'/alunos/{self.ana.pk}/foto/').status_code, 302)  # sem login
        self.client.force_login(self.intruso)
        self.assertEqual(self.client.get(f'/alunos/{self.ana.pk}/foto/').status_code, 404)
        self.assertEqual(self.client.get(f'/alunos/{self.ana.pk}/documentos/{documento.pk}/').status_code, 404)
        self.assertEqual(self.client.post(f'/alunos/{self.ana.pk}/documentos/{documento.pk}/excluir/').status_code, 404)

    def test_excluir_aluno_apaga_os_arquivos(self):
        import os
        from atletas.arquivos import preparar_foto

        self.ana.trocar_foto(preparar_foto(imagem()))
        documento = DocumentoAtleta.objects.create(academia=self.a, atleta=self.ana, tipo='cpf', arquivo=pdf())
        caminhos = [self.ana.foto.path, documento.arquivo.path]
        with self.captureOnCommitCallbacks(execute=True):
            self.ana.delete()
        self.assertFalse(any(os.path.exists(c) for c in caminhos))

    def test_trocar_foto_apaga_a_anterior(self):
        import os
        from atletas.arquivos import preparar_foto

        self.ana.trocar_foto(preparar_foto(imagem()))
        antiga = self.ana.foto.path
        self.ana.trocar_foto(preparar_foto(imagem('JPEG', nome='b.jpg')))
        self.assertFalse(os.path.exists(antiga))
        self.assertTrue(os.path.exists(self.ana.foto.path))


class VolumeDosArquivos(TestCase):
    @override_settings(DEBUG=False, MEDIA_EXIGE_VOLUME=True, MEDIA_ROOT=tempfile.gettempdir())
    def test_avisa_sem_volume(self):
        from atletas.checks import volume_dos_arquivos

        self.assertEqual([a.id for a in volume_dos_arquivos(None)], ['arquivos.W001'])

    @override_settings(DEBUG=False, MEDIA_EXIGE_VOLUME=False)
    def test_aviso_pode_ser_desligado(self):
        from atletas.checks import volume_dos_arquivos

        self.assertEqual(volume_dos_arquivos(None), [])
