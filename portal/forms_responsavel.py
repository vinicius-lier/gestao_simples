from django import forms
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

from atletas.models import Responsavel
from integracoes.whatsapp import normalizar_telefone
from .forms import digits


def responsaveis_pelo_login(identificacao):
    """Responsáveis (de academias ativas) cujo CPF ou WhatsApp é o
    informado. Um número de 11 dígitos pode ser CPF ou celular com DDD:
    confere os dois."""
    numeros = digits(identificacao or '')
    if len(numeros) < 10:
        return []
    telefone = normalizar_telefone(numeros)
    return [
        r for r in Responsavel.objects.filter(academia__ativo=True).select_related('academia')
        if (r.cpf and digits(r.cpf) == numeros)
        or (r.whatsapp and normalizar_telefone(r.whatsapp) == telefone)
    ]


_IDENTIFICACAO = forms.TextInput(attrs={
    'class': 'form-control', 'inputmode': 'numeric', 'autocomplete': 'username',
    'placeholder': 'Só números, com DDD no WhatsApp',
})


class LoginResponsavelForm(forms.Form):
    identificacao = forms.CharField(label='CPF ou WhatsApp', max_length=20, widget=_IDENTIFICACAO)
    senha = forms.CharField(label='Senha', strip=False, widget=forms.PasswordInput(attrs={
        'class': 'form-control', 'autocomplete': 'current-password',
    }))

    ERRO = 'CPF/WhatsApp ou senha incorretos.'

    def clean(self):
        data = super().clean()
        if self.errors:
            return data
        candidatos = responsaveis_pelo_login(data['identificacao'])
        if any(r.bloqueado for r in candidatos):
            raise ValidationError(
                'Muitas tentativas com senha errada. Tente de novo em 15 minutos ou use "Esqueci a senha".'
            )
        # Mesmo CPF/WhatsApp em mais de um cadastro (irmãos, duas escolas):
        # entra no que tiver esta senha.
        for responsavel in candidatos:
            if responsavel.tem_senha and responsavel.conferir_senha(data['senha']):
                self.responsavel = responsavel
                return data
        raise ValidationError(self.ERRO)


class EsqueciSenhaForm(forms.Form):
    identificacao = forms.CharField(label='CPF ou WhatsApp', max_length=20, widget=_IDENTIFICACAO)


class SenhaResponsavelForm(forms.Form):
    """Criar ou trocar a senha. A senha atual só é pedida quando a família
    entrou com senha — quem entrou pelo link do WhatsApp já provou que é o
    dono do número (é o caminho do "esqueci a senha")."""

    senha_atual = forms.CharField(label='Senha atual', strip=False, widget=forms.PasswordInput)
    nova_senha = forms.CharField(
        label='Nova senha', strip=False, widget=forms.PasswordInput,
        help_text='Pelo menos 8 caracteres, sem ser só números.',
    )
    confirmacao = forms.CharField(label='Repita a nova senha', strip=False, widget=forms.PasswordInput)

    def __init__(self, *args, responsavel, pedir_senha_atual, **kwargs):
        super().__init__(*args, **kwargs)
        self.responsavel = responsavel
        if not pedir_senha_atual:
            del self.fields['senha_atual']
        for nome, field in self.fields.items():
            field.widget.attrs['class'] = 'form-control'
            field.widget.attrs['autocomplete'] = 'current-password' if nome == 'senha_atual' else 'new-password'

    def clean_senha_atual(self):
        senha = self.cleaned_data['senha_atual']
        if not self.responsavel.conferir_senha(senha):
            raise ValidationError('Senha atual incorreta.')
        return senha

    def clean(self):
        data = super().clean()
        nova, confirmacao = data.get('nova_senha'), data.get('confirmacao')
        if nova and confirmacao and nova != confirmacao:
            self.add_error('confirmacao', 'As senhas não conferem.')
        elif nova:
            try:
                validate_password(nova)
            except ValidationError as erro:
                self.add_error('nova_senha', erro)
        return data
