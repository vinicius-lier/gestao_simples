import uuid
from decimal import Decimal
from django import forms
from django.core import signing
from django.core.exceptions import ValidationError
from django.utils import timezone

from financeiro.models import Mensalidade
from matriculas.models import Matricula
from .forms_matricula import _estilo_painel


class CobrancaAvulsaForm(forms.Form):
    matricula = forms.ModelChoiceField(queryset=Matricula.objects.none(), label='Aluno / matrícula')
    tipo = forms.ChoiceField(label='Tipo de cobrança', choices=[x for x in Mensalidade.TIPOS if x[0] in Mensalidade.TIPOS_AVULSOS])
    descricao = forms.CharField(label='Descrição', max_length=150, required=False,
        help_text='Ex.: judogui tamanho A2, exame para faixa amarela ou material comprado.')
    valor = forms.DecimalField(label='Valor (R$)', min_value=Decimal('0.01'), max_digits=10, decimal_places=2)
    vencimento = forms.DateField(label='Vencimento desta compra', widget=forms.DateInput(format='%Y-%m-%d', attrs={'type':'date'}))
    chave = forms.CharField(widget=forms.HiddenInput())

    def __init__(self, *args, academia, usuario, **kwargs):
        super().__init__(*args, **kwargs)
        self.academia = academia
        self.usuario = usuario
        self.fields['matricula'].queryset = Matricula.objects.filter(
            academia=academia, atleta__academia=academia, modalidade__academia=academia,
        ).select_related('atleta','modalidade')
        self.fields['vencimento'].initial = timezone.localdate()
        self.fields['chave'].initial = signing.dumps({'academia':academia.pk,'usuario':usuario.pk,'nonce':str(uuid.uuid4())}, salt='cobranca-avulsa')
        _estilo_painel(self)

    def clean_chave(self):
        try:
            payload = signing.loads(self.cleaned_data['chave'], salt='cobranca-avulsa', max_age=86400)
            if payload['academia'] != self.academia.pk or payload['usuario'] != self.usuario.pk:
                raise ValueError
            return uuid.UUID(payload['nonce'])
        except (signing.BadSignature, ValueError, KeyError, TypeError):
            raise ValidationError('O formulário expirou. Abra uma nova cobrança e tente novamente.')
