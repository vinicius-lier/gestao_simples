from django import forms
from modalidades.models import Turma
from .models import AulaExperimental, ConfiguracaoExperimental, InscricaoExperimental


def estilizar(form):
    for field in form.fields.values():
        field.widget.attrs['class'] = 'form-check-input' if isinstance(field.widget, forms.CheckboxInput) else 'form-control'


class ConfiguracaoExperimentalForm(forms.ModelForm):
    class Meta:
        model = ConfiguracaoExperimental
        fields = ['ativo', 'vagas_padrao', 'antecedencia_horas_padrao', 'janela_dias', 'fila_habilitada_padrao']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        estilizar(self)


class AulaExperimentalForm(forms.ModelForm):
    class Meta:
        model = AulaExperimental
        fields = ['turma', 'inicio', 'fim', 'vagas', 'inscricoes_abrem', 'antecedencia_horas', 'fila_habilitada', 'ativa']
        widgets = {k: forms.DateTimeInput(format='%Y-%m-%dT%H:%M', attrs={'type': 'datetime-local'}) for k in ['inicio', 'fim', 'inscricoes_abrem']}

    def __init__(self, *args, academia, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['turma'].queryset = Turma.objects.filter(academia=academia, ativo=True, unidade__academia=academia, unidade__ativo=True, modalidade__academia=academia, modalidade__ativo=True)
        if self.instance.pk is None:
            # Novas aulas herdam os padrões da academia (a equipe pode sobrescrever).
            cfg = ConfiguracaoExperimental.resolver(academia)
            self.fields['vagas'].initial = cfg.vagas_padrao
            self.fields['antecedencia_horas'].initial = cfg.antecedencia_horas_padrao
            self.fields['fila_habilitada'].initial = cfg.fila_habilitada_padrao
        estilizar(self)


class InscricaoExperimentalForm(forms.ModelForm):
    aceitar_fila = forms.BooleanField(required=False, label='Se não houver vaga, quero entrar na fila de espera. Sei que isso não confirma minha participação.')

    class Meta:
        model = InscricaoExperimental
        fields = ['nome', 'idade', 'responsavel', 'telefone', 'email']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        estilizar(self)

    def clean_nome(self):
        return ' '.join(self.cleaned_data['nome'].split())

    def clean_telefone(self):
        value = ''.join(c for c in self.cleaned_data['telefone'] if c.isascii() and c.isdigit())
        if not 10 <= len(value) <= 13:
            raise forms.ValidationError('Informe um telefone válido com DDD.')
        return value

    def clean(self):
        data = super().clean()
        if data.get('idade', 18) < 18 and not data.get('responsavel'):
            self.add_error('responsavel', 'Informe o nome do responsável pelo menor.')
        return data
