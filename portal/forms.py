from django import forms
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from academias.models import Academia, Unidade
from atletas.models import Atleta, Responsavel
from matriculas.models import Matricula
from servicos.models import Servico, Turma, Professor, Graduacao


def digits(value):
    return ''.join(c for c in value if c.isdigit())


class AlunoForm(forms.ModelForm):
    proprio_responsavel = forms.BooleanField(required=False, label='O próprio aluno é o responsável financeiro')
    aluno_whatsapp = forms.CharField(max_length=20, required=False, label='WhatsApp do aluno')
    aluno_email = forms.EmailField(required=False, label='E-mail do aluno')
    responsavel = forms.ModelChoiceField(queryset=Responsavel.objects.none(), required=False, label='Responsável existente')
    responsavel_nome = forms.CharField(max_length=150, required=False, label='Nome do novo responsável')
    responsavel_cpf = forms.CharField(max_length=14, required=False, label='CPF do novo responsável')
    responsavel_whatsapp = forms.CharField(max_length=20, required=False, label='WhatsApp do novo responsável')
    responsavel_email = forms.EmailField(required=False, label='E-mail do novo responsável')

    class Meta:
        model = Atleta
        fields = ('nome', 'data_nascimento', 'cpf', 'faixa', 'status', 'observacoes', 'proprio_responsavel')
        widgets = {'data_nascimento': forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'})}

    def __init__(self, *args, academia, **kwargs):
        super().__init__(*args, **kwargs)
        self.academia = academia
        self.instance.academia = academia
        self.fields['responsavel'].queryset = Responsavel.objects.filter(academia=academia).order_by('nome')
        if self.instance.pk:
            if self.instance.proprio_responsavel:
                self.initial['proprio_responsavel'] = True
                r = self.instance.responsavel_financeiro
                if r and r.academia_id == academia.pk:
                    self.initial['aluno_whatsapp'] = r.whatsapp
                    self.initial['aluno_email'] = r.email
            else:
                self.initial['responsavel'] = self.instance.responsavel_financeiro_id
        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-check-input' if isinstance(field.widget, forms.CheckboxInput) else 'form-select' if isinstance(field.widget, forms.Select) else 'form-control'

    def clean_data_nascimento(self):
        value = self.cleaned_data['data_nascimento']
        if value and value > timezone.localdate():
            raise ValidationError('O nascimento não pode estar no futuro.')
        return value

    def clean_responsavel_whatsapp(self):
        value = self.cleaned_data['responsavel_whatsapp']
        if value and not digits(value):
            raise ValidationError('Informe um telefone com números.')
        return value

    def clean(self):
        data = super().clean()
        if data.get('proprio_responsavel'):
            if not digits(data.get('cpf', '')):
                self.add_error('cpf', 'Informe o CPF do aluno responsável financeiro.')
            if not digits(data.get('aluno_whatsapp', '')):
                self.add_error('aluno_whatsapp', 'Informe o WhatsApp do aluno.')
            return data
        if data.get('responsavel'):
            if any(data.get(k) for k in ('responsavel_nome', 'responsavel_cpf', 'responsavel_whatsapp', 'responsavel_email')):
                raise ValidationError('Selecione um responsável existente ou preencha um novo responsável.')
        else:
            for key in ('responsavel_nome', 'responsavel_whatsapp'):
                if not data.get(key):
                    self.add_error(key, 'Obrigatório para cadastrar um responsável.')
        return data

    def save(self, commit=True):
        if not commit:
            raise ValueError('O fluxo exige gravação atômica.')
        with transaction.atomic():
            # Serialize creation of guardians within one tenant.
            Academia.objects.select_for_update().get(pk=self.academia.pk)
            if self.cleaned_data.get('proprio_responsavel'):
                self.cleaned_data['responsavel'] = None
                self.cleaned_data['responsavel_nome'] = self.cleaned_data['nome']
                self.cleaned_data['responsavel_cpf'] = self.cleaned_data['cpf']
                self.cleaned_data['responsavel_whatsapp'] = self.cleaned_data['aluno_whatsapp']
                self.cleaned_data['responsavel_email'] = self.cleaned_data['aluno_email']
            responsavel = self.cleaned_data.get('responsavel')
            if not responsavel:
                cpf = digits(self.cleaned_data.get('responsavel_cpf', ''))
                nome = self.cleaned_data['responsavel_nome'].strip()
                whatsapp = digits(self.cleaned_data['responsavel_whatsapp'])
                matches = [r for r in Responsavel.objects.filter(academia=self.academia)
                           if (cpf and digits(r.cpf) == cpf) or
                           (not cpf and r.nome.strip().casefold() == nome.casefold() and digits(r.whatsapp) == whatsapp)]
                if len(matches) > 1:
                    raise ValidationError('Há responsáveis duplicados. Selecione o responsável existente.')
                responsavel = matches[0] if matches else Responsavel.objects.create(
                    academia=self.academia, nome=nome, cpf=cpf, whatsapp=whatsapp,
                    email=self.cleaned_data.get('responsavel_email', ''))
            self.instance.responsavel_financeiro = responsavel
            self.instance.full_clean()
            return super().save()


class MatriculaForm(forms.ModelForm):
    class Meta:
        model = Matricula
        fields = ('unidade', 'servico', 'turma', 'valor_mensalidade', 'dia_vencimento', 'data_inicio', 'data_fim', 'ativo')
        widgets = {key: forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}) for key in ('data_inicio', 'data_fim')}

    def __init__(self, *args, academia, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.academia = academia
        self.fields['unidade'].queryset = Unidade.objects.filter(academia=academia)
        self.fields['unidade'].required = self.fields['unidade'].queryset.exists()
        self.fields['servico'].label = 'Modalidade'
        self.fields['servico'].queryset = Servico.objects.filter(academia=academia)
        self.fields['turma'].queryset = Turma.objects.filter(academia=academia, servico__academia=academia)
        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-check-input' if isinstance(field.widget, forms.CheckboxInput) else 'form-select' if isinstance(field.widget, forms.Select) else 'form-control'

    def clean_valor_mensalidade(self):
        value = self.cleaned_data['valor_mensalidade']
        if value < 0:
            raise ValidationError('O valor não pode ser negativo.')
        return value


class CadastroAcademiaForm(forms.ModelForm):
    def __init__(self, *args, academia, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.academia = academia
        for name, field in self.fields.items():
            field.widget.attrs['class'] = 'form-check-input' if isinstance(field.widget, forms.CheckboxInput) else 'form-select' if isinstance(field.widget, forms.Select) else 'form-control'
        for name, model in [('unidade', Unidade), ('servico', Servico), ('docente', Professor)]:
            if name in self.fields:
                self.fields[name].queryset = model.objects.filter(academia=academia)
                self.fields[name].required = True


class UnidadeForm(CadastroAcademiaForm):
    class Meta:
        model = Unidade
        fields = ('nome', 'endereco', 'telefone', 'ativo')


class ProfessorForm(CadastroAcademiaForm):
    graduacao = forms.ChoiceField(label='Graduação do cadastro anterior', choices=[('', '---------')], required=False)

    def __init__(self, *args, academia, **kwargs):
        super().__init__(*args, academia=academia, **kwargs)
        self.fields['faixa'].queryset = Graduacao.objects.filter(academia=academia, modalidade__academia=academia)
        self.fields['faixa'].help_text = 'Cadastre as faixas em Graduações / faixas. Cada opção identifica sua modalidade.'
        if self.instance.graduacao:
            self.fields['graduacao'].choices = [('', '---------'), (self.instance.graduacao, self.instance.graduacao)]
            self.initial['graduacao'] = self.instance.graduacao
        else:
            self.fields['graduacao'].widget = forms.HiddenInput()

    class Meta:
        model = Professor
        fields = ('nome', 'email', 'telefone', 'faixa', 'graduacao', 'ativo')


class GraduacaoForm(CadastroAcademiaForm):
    class Meta:
        model = Graduacao
        fields = ('modalidade', 'nome', 'ordem', 'ativo')
        labels = {'nome': 'Nome da faixa / graduação', 'ordem': 'Ordem de evolução'}
        help_texts = {'nome': 'Ex.: Branca, Azul, Preta — 1º dan. Use a nomenclatura da modalidade.'}

    def __init__(self, *args, academia, **kwargs):
        super().__init__(*args, academia=academia, **kwargs)
        self.fields['modalidade'].queryset = Servico.objects.filter(academia=academia)


class TurmaForm(CadastroAcademiaForm):
    class Meta:
        model = Turma
        labels = {'servico': 'Modalidade'}
        fields = ('nome', 'unidade', 'servico', 'docente', 'dias_semana', 'horario', 'local', 'ativo')
        widgets = {'horario': forms.TimeInput(format='%H:%M', attrs={'type': 'time'})}


class ServicoForm(CadastroAcademiaForm):
    class Meta:
        model = Servico
        fields = ('nome', 'descricao', 'valor_padrao', 'dia_vencimento', 'ativo')
        labels = {'descricao': 'Descrição', 'valor_padrao': 'Valor padrão da mensalidade (R$)', 'dia_vencimento': 'Dia de vencimento'}

    def clean_valor_padrao(self):
        valor = self.cleaned_data['valor_padrao']
        if valor < 0:
            raise ValidationError('O valor não pode ser negativo.')
        return valor

    def clean_dia_vencimento(self):
        dia = self.cleaned_data['dia_vencimento']
        if not 1 <= dia <= 31:
            raise ValidationError('Informe um dia entre 1 e 31.')
        return dia
