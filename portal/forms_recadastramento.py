from datetime import timedelta

from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone

from academias.models import Academia
from atletas.arquivos import preparar_foto, validar_documento
from atletas.models import DocumentoAtleta
from modalidades.models import Turma
from .forms_matricula import MatriculaPublicaForm, _estilo_painel

ACEITA_FOTO = 'image/jpeg,image/png,image/webp'
ACEITA_DOCUMENTO = 'application/pdf,image/*,.heic,.heif'


class CampanhaForm(forms.Form):
    titulo = forms.CharField(label='Título', max_length=100)
    prazo = forms.DateField(
        label='Responder até', widget=forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}),
    )
    turma = forms.ModelChoiceField(
        queryset=Turma.objects.none(), required=False, label='Quem recebe',
        empty_label='Todos os alunos ativos',
    )
    pedir_foto = forms.BooleanField(label='Pedir uma foto do aluno', required=False, initial=True)
    documentos = forms.MultipleChoiceField(
        label='Documentos pedidos', required=False, widget=forms.CheckboxSelectMultiple,
        choices=[(DocumentoAtleta.LAUDO, 'Laudo ou documento médico (opcional)')],
        initial=[DocumentoAtleta.LAUDO], disabled=True,
    )
    mensagem = forms.CharField(
        label='Recado para as famílias (opcional)', required=False,
        widget=forms.Textarea(attrs={'rows': 3}),
        help_text='Aparece no início da página do recadastramento.',
    )
    enviar_agora = forms.BooleanField(
        label='Enviar o link pelo WhatsApp agora', required=False, initial=True,
        help_text='Uma mensagem por família, com uma pausa entre elas. Desmarcado: você envia depois.',
    )

    def __init__(self, *args, academia, **kwargs):
        super().__init__(*args, **kwargs)
        hoje = timezone.localdate()
        self.fields['titulo'].initial = f'Recadastramento {hoje.year}'
        self.fields['prazo'].initial = hoje + timedelta(days=30)
        self.fields['turma'].queryset = Turma.objects.filter(
            academia=academia, ativo=True, modalidade__academia=academia,
        ).select_related('modalidade')
        _estilo_painel(self)
        self.fields['documentos'].widget.attrs.pop('class', None)

    def clean_prazo(self):
        prazo = self.cleaned_data['prazo']
        if prazo < timezone.localdate():
            raise ValidationError('O prazo não pode ser anterior a hoje.')
        return prazo


class RecadastramentoAnualForm(forms.ModelForm):
    class Meta:
        model = Academia
        fields = ['recadastramento_mes']
        labels = {'recadastramento_mes': 'Abrir sozinho todo ano em'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['recadastramento_mes'].choices = [('', 'Não abrir sozinho')] + [
            c for c in self.fields['recadastramento_mes'].choices if c[0] != ''
        ]
        self.fields['recadastramento_mes'].help_text = (
            'No dia 1º do mês, o recadastramento do ano é aberto para todos os alunos ativos e o link vai '
            'pelo WhatsApp. A família informa os números de RG e CPF, endereço e saúde. Laudos médicos são opcionais; a foto segue a configuração da escola. '
            'Não abre se já houver um nos últimos 90 dias.'
        )
        _estilo_painel(self)


class RecadastramentoPublicoForm(MatriculaPublicaForm):
    """A ficha de matrícula de novo, já preenchida com o cadastro atual, sem
    o dia de vencimento (a cobrança não muda aqui), com foto e documentos."""

    def __init__(self, *args, pedido, **kwargs):
        super().__init__(*args, academia=pedido.academia, **kwargs)
        self.pedido = pedido
        campanha = pedido.campanha
        del self.fields['vencimento_preferido']
        self.tem_foto = bool(pedido.atleta.foto)
        self.campos_documento = ['documento_laudo']
        self.fields['endereco'].required = True
        self.fields['rg'].required = True
        if campanha.pedir_foto:
            self.fields['foto'] = forms.FileField(
                label='Foto do aluno', required=not self.tem_foto,
                widget=forms.ClearableFileInput(attrs={'accept': ACEITA_FOTO}),
                help_text=(
                    'Já temos uma foto. Envie outra só se quiser trocar.' if self.tem_foto
                    else 'Foto de rosto, de frente e com boa luz (JPG ou PNG).'
                ),
            )
        if 'aceite_declaracao' in self.fields:
            self.fields['aceite_declaracao'] = self.fields.pop('aceite_declaracao')

    def clean_foto(self):
        arquivo = self.cleaned_data.get('foto')
        if not arquivo:
            return None
        return preparar_foto(arquivo)

    def campos_de_documento(self):
        return [self[nome] for nome in self.campos_documento]

    def documentos(self):
        """[(tipo, arquivo)] dos documentos enviados."""
        enviados = []
        for nome in self.campos_documento:
            arquivo = self.cleaned_data.get(nome)
            if arquivo:
                enviados.append((nome.removeprefix('documento_'), arquivo))
        return enviados


class FotoForm(forms.Form):
    foto = forms.FileField(label='Foto', widget=forms.ClearableFileInput(attrs={'accept': ACEITA_FOTO}))

    def clean_foto(self):
        return preparar_foto(self.cleaned_data['foto'])


class DocumentoForm(forms.ModelForm):
    class Meta:
        model = DocumentoAtleta
        fields = ['tipo', 'arquivo', 'descricao', 'validade']
        widgets = {
            'validade': forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}),
            'arquivo': forms.ClearableFileInput(attrs={'accept': ACEITA_DOCUMENTO}),
        }
        labels = {'arquivo': 'Arquivo (PDF ou foto, até 10 MB)', 'descricao': 'Descrição (opcional)'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _estilo_painel(self)
