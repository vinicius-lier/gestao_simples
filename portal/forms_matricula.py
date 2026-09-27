from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone

from academias.models import Unidade
from modalidades.models import Modalidade, Turma
from .forms import cpf_valido, digits
from .models import ConviteMatricula


def _estilo_painel(form):
    for field in form.fields.values():
        if isinstance(field.widget, forms.CheckboxInput):
            field.widget.attrs['class'] = 'form-check-input'
        elif isinstance(field.widget, forms.Select):
            field.widget.attrs['class'] = 'form-select'
        else:
            field.widget.attrs['class'] = 'form-control'


class ConviteMatriculaForm(forms.ModelForm):
    validade_dias = forms.IntegerField(label='Validade do link (dias)', min_value=1, max_value=60, initial=7)

    class Meta:
        model = ConviteMatricula
        fields = ['unidade', 'modalidade', 'turma', 'convidado_nome', 'convidado_whatsapp',
                  'observacao_interna', 'valor_mensalidade', 'dia_vencimento']

    def __init__(self, *args, academia, **kwargs):
        super().__init__(*args, **kwargs)
        self.academia = academia
        unidades = Unidade.objects.filter(academia=academia, ativo=True)
        self.fields['unidade'].queryset = unidades
        self.fields['unidade'].required = unidades.exists()
        self.fields['modalidade'].queryset = Modalidade.objects.filter(academia=academia, ativo=True)
        self.fields['turma'].queryset = Turma.objects.filter(
            academia=academia, ativo=True, modalidade__academia=academia
        ).select_related('modalidade', 'unidade')
        self.fields['turma'].required = True
        self.fields['valor_mensalidade'].required = False
        self.fields['dia_vencimento'].required = False
        self.fields['valor_mensalidade'].help_text = 'Em branco: usa o valor da turma.'
        self.fields['dia_vencimento'].help_text = 'Em branco: usa o vencimento da turma.'
        self.fields['convidado_whatsapp'].help_text = (
            'Com DDD. Informe para poder enviar o link direto pelo WhatsApp, sem sair do app.'
        )
        _estilo_painel(self)

    def clean_convidado_whatsapp(self):
        valor = self.cleaned_data.get('convidado_whatsapp', '')
        if valor and not 10 <= len(digits(valor)) <= 13:
            raise ValidationError('Informe um WhatsApp válido com DDD.')
        return valor

    def clean_dia_vencimento(self):
        dia = self.cleaned_data.get('dia_vencimento')
        if dia is not None and not 1 <= dia <= 31:
            raise ValidationError('Informe um dia entre 1 e 31.')
        return dia

    def clean_valor_mensalidade(self):
        valor = self.cleaned_data.get('valor_mensalidade')
        if valor is not None and valor < 0:
            raise ValidationError('O valor não pode ser negativo.')
        return valor

    def clean(self):
        data = super().clean()
        turma, modalidade, unidade = data.get('turma'), data.get('modalidade'), data.get('unidade')
        if turma:
            if modalidade and turma.modalidade_id != modalidade.pk:
                self.add_error('turma', 'A turma não pertence à modalidade selecionada.')
            if unidade and turma.unidade_id and turma.unidade_id != unidade.pk:
                self.add_error('turma', 'A turma não pertence à unidade selecionada.')
            elif not unidade and turma.unidade_id:
                data['unidade'] = turma.unidade
            if data.get('valor_mensalidade') is None and turma.valor_mensalidade is None:
                self.add_error('valor_mensalidade', 'A turma não tem valor definido — informe o valor da mensalidade.')
        return data

    def save(self, criado_por=None):
        d = self.cleaned_data
        return ConviteMatricula.gerar(
            academia=self.academia,
            unidade=d.get('unidade'),
            modalidade=d['modalidade'],
            turma=d.get('turma'),
            criado_por=criado_por,
            convidado_nome=d.get('convidado_nome', ''),
            convidado_whatsapp=d.get('convidado_whatsapp', ''),
            observacao_interna=d.get('observacao_interna', ''),
            valor_mensalidade=d.get('valor_mensalidade'),
            dia_vencimento=d.get('dia_vencimento'),
            validade_dias=d['validade_dias'],
        )


class AtivacaoMatriculaForm(forms.Form):
    """O administrador confere a matrícula e define o vencimento ao ativar.
    Vem preenchido com o dia de vencimento da matrícula e a sugestão de 1º
    vencimento (o próximo a partir de hoje)."""

    dia_vencimento = forms.IntegerField(label='Dia de vencimento', min_value=1, max_value=31)
    primeiro_vencimento = forms.DateField(
        label='Primeiro vencimento',
        widget=forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}),
    )

    def __init__(self, *args, matricula, **kwargs):
        from financeiro.services import proximo_vencimento

        super().__init__(*args, **kwargs)
        escolhido = matricula.primeiro_vencimento
        if escolhido is None or escolhido < timezone.localdate():
            escolhido = proximo_vencimento(matricula)
        self.fields['dia_vencimento'].initial = matricula.dia_vencimento
        self.fields['primeiro_vencimento'].initial = escolhido
        _estilo_painel(self)

    def clean_primeiro_vencimento(self):
        valor = self.cleaned_data['primeiro_vencimento']
        if valor < timezone.localdate():
            raise ValidationError('O primeiro vencimento não pode ser anterior a hoje.')
        return valor


class MatriculaPublicaForm(forms.Form):
    nome = forms.CharField(label='Nome completo do aluno', max_length=150)
    data_nascimento = forms.DateField(
        label='Data de nascimento', required=False,
        widget=forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}),
    )
    cpf = forms.CharField(label='CPF do aluno', max_length=14, required=False)
    faixa = forms.CharField(label='Faixa / graduação atual', max_length=50, required=False)
    observacoes = forms.CharField(label='Observações (saúde, restrições, etc.)', required=False, widget=forms.Textarea(attrs={'rows': 3}))

    proprio_responsavel = forms.BooleanField(label='O próprio aluno é o responsável financeiro', required=False)
    responsavel_nome = forms.CharField(label='Nome do responsável', max_length=150, required=False)
    responsavel_cpf = forms.CharField(label='CPF do responsável', max_length=14, required=False)
    responsavel_whatsapp = forms.CharField(label='WhatsApp para contato (com DDD)', max_length=20, required=False)
    responsavel_email = forms.EmailField(label='E-mail', required=False)

    def clean_nome(self):
        return ' '.join(self.cleaned_data['nome'].split())

    def clean_data_nascimento(self):
        value = self.cleaned_data.get('data_nascimento')
        if value and value > timezone.localdate():
            raise ValidationError('A data de nascimento não pode estar no futuro.')
        return value

    def clean_cpf(self):
        value = self.cleaned_data.get('cpf', '')
        if value and not cpf_valido(value):
            raise ValidationError('CPF inválido. Confira os números digitados.')
        return value

    def clean_responsavel_cpf(self):
        value = self.cleaned_data.get('responsavel_cpf', '')
        if value and not cpf_valido(value):
            raise ValidationError('CPF inválido. Confira os números digitados.')
        return value

    def clean_responsavel_whatsapp(self):
        value = self.cleaned_data.get('responsavel_whatsapp', '')
        if value and not 10 <= len(digits(value)) <= 13:
            raise ValidationError('Informe um WhatsApp válido com DDD.')
        return value

    def clean(self):
        data = super().clean()
        proprio = data.get('proprio_responsavel')
        if not digits(data.get('responsavel_whatsapp', '')):
            self.add_error('responsavel_whatsapp', 'Informe um WhatsApp para contato.')
        if proprio:
            if not self.errors.get('cpf') and not digits(data.get('cpf', '')):
                self.add_error('cpf', 'Informe o CPF do aluno (ele será o responsável financeiro).')
        elif not data.get('responsavel_nome'):
            self.add_error('responsavel_nome', 'Informe o nome do responsável.')
        return data
