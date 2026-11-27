from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone

from academias.models import Unidade
from atletas.arquivos import validar_documento, preparar_foto
from modalidades.models import Modalidade, Turma
from .forms import _nao_negativo, cpf_valido, digits
from .models import ConviteMatricula
from .models_matricula import (
    ACEITE_DECLARACAO, ACEITE_TERMOS, PERGUNTAS_SAUDE, VENCIMENTOS_FICHA,
)


def _estilo_painel(form):
    for field in form.fields.values():
        if isinstance(field.widget, forms.CheckboxInput):
            field.widget.attrs['class'] = 'form-check-input'
        elif isinstance(field.widget, forms.Select):
            field.widget.attrs['class'] = 'form-select'
        else:
            field.widget.attrs['class'] = 'form-control'


class ConviteMatriculaForm(forms.ModelForm):
    validade_dias = forms.IntegerField(
        label='Validade do link (dias)', min_value=1, max_value=60, initial=7, required=False,
        help_text='Não vale para o link permanente, que fica aberto até ser cancelado.',
    )

    class Meta:
        model = ConviteMatricula
        fields = ['permanente', 'unidade', 'modalidade', 'turma', 'convidado_nome', 'convidado_whatsapp',
                  'observacao_interna', 'valor_mensalidade', 'valor_apos_vencimento', 'taxa_matricula',
                  'dia_vencimento']

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
        self.fields['turma'].required = False
        self.fields['turma'].empty_label = 'Sem turma (informar o valor abaixo)'
        self.fields['valor_mensalidade'].required = False
        self.fields['dia_vencimento'].required = False
        self.fields['valor_mensalidade'].help_text = 'Em branco: usa o valor da turma.'
        self.fields['valor_apos_vencimento'].help_text = 'Em branco: usa o da turma.'
        self.fields['taxa_matricula'].help_text = 'Gerada quando a matrícula é ativada. Em branco: usa a da turma; 0 para não cobrar.'
        self.fields['dia_vencimento'].help_text = (
            'Em branco: a família escolhe na ficha (dia 5, 10 ou 15) ou vale o vencimento da turma.'
        )
        if academia.dia_vencimento_fixo:
            self.initial['dia_vencimento'] = academia.dia_vencimento_fixo
            self.fields['dia_vencimento'].disabled = True
            self.fields['dia_vencimento'].help_text = 'Vencimento fixo da escola.'
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

    def clean_valor_apos_vencimento(self):
        return _nao_negativo(self.cleaned_data.get('valor_apos_vencimento'))

    def clean_taxa_matricula(self):
        return _nao_negativo(self.cleaned_data.get('taxa_matricula'))

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
        elif 'turma' in data and data.get('valor_mensalidade') is None:
            self.add_error('valor_mensalidade', 'Sem turma, informe o valor da mensalidade.')
        if not data.get('permanente') and not data.get('validade_dias'):
            self.add_error('validade_dias', 'Informe por quantos dias o link vale.')
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
            valor_apos_vencimento=d.get('valor_apos_vencimento'),
            taxa_matricula=d.get('taxa_matricula'),
            dia_vencimento=d.get('dia_vencimento'),
            validade_dias=d.get('validade_dias') or 7,
            permanente=bool(d.get('permanente')),
        )


class ImportacaoFichasForm(forms.Form):
    """Planilha de respostas do formulário antigo + com o que os alunos novos
    são matriculados (uma turma, ou sem turma com modalidade e valores) e a
    data em que a cobrança deles começa."""

    TAMANHO_MAXIMO = 5 * 1024 * 1024

    arquivo = forms.FileField(
        label='Planilha de respostas (.xlsx)',
        help_text='No Google Forms: Respostas → Ver no Planilhas → Arquivo → Fazer download → Microsoft Excel (.xlsx).',
    )
    turma = forms.ModelChoiceField(
        queryset=Turma.objects.none(), label='Turma dos alunos novos', required=False,
        empty_label='Sem turma (informar modalidade e valores abaixo)',
        help_text='Com turma, a modalidade, o polo e os valores vêm dela e os campos abaixo são ignorados.',
    )
    modalidade = forms.ModelChoiceField(queryset=Modalidade.objects.none(), label='Modalidade', required=False)
    unidade = forms.ModelChoiceField(queryset=Unidade.objects.none(), label='Polo / unidade', required=False)
    valor_mensalidade = forms.DecimalField(
        label='Mensalidade até o vencimento (R$)', max_digits=10, decimal_places=2, min_value=0, required=False,
    )
    valor_apos_vencimento = forms.DecimalField(
        label='Mensalidade após o vencimento (R$)', max_digits=10, decimal_places=2, min_value=0, required=False,
        help_text='Em branco: o valor não muda depois do vencimento.',
    )
    dia_vencimento = forms.IntegerField(
        label='Dia de vencimento', min_value=1, max_value=31, required=False,
        help_text='O dia em que todos os alunos novos vencem. Em branco: o da turma (ou dia 10, sem turma).',
    )
    usar_dia_da_ficha = forms.BooleanField(
        label='Usar o dia escolhido na ficha (5, 10 ou 15) quando houver', required=False,
        help_text='Desmarcado: todos vencem no dia acima. A escolha da ficha fica guardada no histórico do aluno.',
    )
    cobrar_a_partir_de = forms.DateField(
        label='Cobrar mensalidades que vencem a partir de',
        widget=forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}),
        help_text='A 1ª mensalidade de cada aluno novo é a do primeiro dia de vencimento a partir desta data.',
    )

    def __init__(self, *args, academia, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['turma'].queryset = Turma.objects.filter(
            academia=academia, ativo=True, modalidade__academia=academia,
        ).select_related('modalidade', 'unidade')
        self.fields['modalidade'].queryset = Modalidade.objects.filter(academia=academia, ativo=True)
        self.fields['unidade'].queryset = Unidade.objects.filter(academia=academia, ativo=True)
        self.academia = academia
        if academia.dia_vencimento_fixo:
            self.initial['dia_vencimento'] = academia.dia_vencimento_fixo
            self.fields['dia_vencimento'].disabled = True
            self.fields['dia_vencimento'].help_text = 'Vencimento fixo da escola.'
            self.initial['usar_dia_da_ficha'] = False
            self.fields['usar_dia_da_ficha'].disabled = True
            self.fields['usar_dia_da_ficha'].widget = forms.HiddenInput()
        self.fields['cobrar_a_partir_de'].initial = timezone.localdate()
        _estilo_painel(self)

    def clean_arquivo(self):
        arquivo = self.cleaned_data['arquivo']
        if not arquivo.name.lower().endswith('.xlsx'):
            raise ValidationError('Envie a planilha em formato .xlsx.')
        if arquivo.size > self.TAMANHO_MAXIMO:
            raise ValidationError('A planilha passa de 5 MB.')
        return arquivo

    def clean_turma(self):
        turma = self.cleaned_data.get('turma')
        if turma is not None and turma.valor_mensalidade is None:
            raise ValidationError('Esta turma não tem valor de mensalidade definido.')
        return turma

    def clean_cobrar_a_partir_de(self):
        valor = self.cleaned_data['cobrar_a_partir_de']
        if valor < timezone.localdate():
            raise ValidationError('A data não pode ser anterior a hoje.')
        return valor

    def clean(self):
        data = super().clean()
        if 'turma' in data and data['turma'] is None:
            if not data.get('modalidade'):
                self.add_error('modalidade', 'Sem turma, informe a modalidade.')
            if data.get('valor_mensalidade') is None:
                self.add_error('valor_mensalidade', 'Sem turma, informe o valor da mensalidade.')
        return data

    def condicoes(self):
        """Com o que os alunos novos são matriculados."""
        from .importacao_fichas import CondicoesMatricula

        d = self.cleaned_data
        dia = self.academia.dia_vencimento_fixo or d.get('dia_vencimento')
        usar_ficha = False if self.academia.dia_vencimento_fixo else bool(d.get('usar_dia_da_ficha'))
        if d.get('turma') is not None:
            return CondicoesMatricula.da_turma(d['turma'], dia_vencimento=dia, usar_dia_da_ficha=usar_ficha)
        return CondicoesMatricula(
            modalidade=d['modalidade'], unidade=d.get('unidade'),
            valor_mensalidade=d['valor_mensalidade'], valor_apos_vencimento=d.get('valor_apos_vencimento'),
            dia_vencimento=dia or 10, usar_dia_da_ficha=usar_ficha,
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
        fixo = matricula.academia.dia_vencimento_fixo
        if fixo:
            self.fields['dia_vencimento'].initial = fixo
            self.fields['dia_vencimento'].disabled = True
            if escolhido.day != fixo:
                escolhido = proximo_vencimento(matricula)
        self.fields['primeiro_vencimento'].initial = escolhido
        _estilo_painel(self)

    def clean_primeiro_vencimento(self):
        valor = self.cleaned_data['primeiro_vencimento']
        if valor < timezone.localdate():
            raise ValidationError('O primeiro vencimento não pode ser anterior a hoje.')
        dia = self.fields['dia_vencimento'].initial
        if self.fields['dia_vencimento'].disabled and valor.day != dia:
            raise ValidationError(f'O vencimento deve ser no dia {dia}.')
        return valor


SIM_NAO = [('sim', 'Sim'), ('nao', 'Não')]


class MatriculaPublicaForm(forms.Form):
    """Ficha de matrícula que a família preenche pelo link do convite — as
    mesmas perguntas, na mesma ordem, do formulário que a escola usava. A
    única pergunta a mais é o nome do responsável, que recebe as cobranças.

    Os termos e a declaração vêm da academia (``termos_matricula`` e
    ``declaracao_matricula``); sem texto cadastrado, o aceite não aparece."""

    aceite_termos = forms.BooleanField(label=ACEITE_TERMOS)
    nome = forms.CharField(label='Nome completo do (a) aluno (a):', max_length=150)
    rg = forms.CharField(label='Número do RG', max_length=30, required=False)
    endereco = forms.CharField(label='Endereço completo', required=False,
        widget=forms.Textarea(attrs={'rows': 3}),
        help_text='Rua, número, complemento, bairro, cidade, UF e CEP. Não é necessário enviar comprovante.')
    documento_identidade = forms.FileField(label='Arquivo do RG do atleta', required=False,
        validators=[validar_documento], widget=forms.ClearableFileInput(attrs={'accept':'application/pdf,image/*,.heic,.heif'}))
    foto = forms.FileField(label='Foto do atleta', required=False,
        widget=forms.ClearableFileInput(attrs={'accept':'image/jpeg,image/png,image/webp'}),
        help_text='JPG, PNG ou WEBP. Você pode ajustar o enquadramento antes de enviar.')
    data_nascimento = forms.DateField(
        label='Data de nascimento do (a) aluno (a)',
        widget=forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}),
    )
    federado = forms.TypedChoiceField(
        label='Federado?', required=False, empty_value=None,
        choices=[('', 'Não informado'), ('sim', 'Sim'), ('nao', 'Não')],
        coerce=lambda valor: valor == 'sim',
    )
    numero_zempo = forms.CharField(label='Número Zempo', max_length=40, required=False)
    numero_fjerj = forms.CharField(label='Número FJERJ', max_length=40, required=False)
    proprio_responsavel = forms.BooleanField(required=False, label='O atleta é o próprio responsável financeiro')
    cpf = forms.CharField(label='CPF do atleta', max_length=14, required=False)
    responsavel_nome = forms.CharField(label='Nome do (a) responsável', max_length=150)
    responsavel_cpf = forms.CharField(
        label='CPF do (a) responsável financeiro', max_length=14,
        help_text='De quem paga as mensalidades (ou do aluno, se ele mesmo paga). Vai na cobrança do Pix.',
        widget=forms.TextInput(attrs={'inputmode': 'numeric', 'autocomplete': 'off'}),
    )
    telefone = forms.CharField(
        label='Telefone para contato', max_length=20,
        help_text='WhatsApp com DDD. As cobranças das mensalidades chegam por ele.',
    )
    email = forms.EmailField(label='E-mail para contato')
    autorizados_buscar = forms.CharField(
        label='Quem está autorizado (a) a buscar o (a) aluno (a)?',
        required=False,
        widget=forms.Textarea(attrs={'rows': 2}),
    )
    vencimento_preferido = forms.TypedChoiceField(
        label='Qual é a melhor data para o vencimento da mensalidade?',
        choices=VENCIMENTOS_FICHA, coerce=int, widget=forms.RadioSelect,
    )
    aceite_declaracao = forms.BooleanField(label=ACEITE_DECLARACAO)

    def __init__(self, *args, academia=None, **kwargs):
        super().__init__(*args, **kwargs)
        proprio = self.data.get('proprio_responsavel') in ('on', 'true', 'True', '1') if self.is_bound else bool(self.initial.get('proprio_responsavel'))
        for campo in ('responsavel_nome', 'responsavel_cpf'):
            self.fields[campo].required = not proprio
            self.fields[campo].disabled = proprio
        self.fields['cpf'].required = proprio
        self.fields['cpf'].disabled = not proprio
        self.termos = (academia.termos_matricula if academia else '').strip()
        self.declaracao = (academia.declaracao_matricula if academia else '').strip()
        if academia and academia.dia_vencimento_fixo:
            dia = academia.dia_vencimento_fixo
            self.fields['vencimento_preferido'].choices = [(dia, f'Dia {dia}')]
            self.fields['vencimento_preferido'].initial = dia
            self.fields['vencimento_preferido'].disabled = True
            self.fields['vencimento_preferido'].widget = forms.HiddenInput()
            self.vencimento_fixo = dia
        if not self.termos:
            del self.fields['aceite_termos']
        if not self.declaracao:
            del self.fields['aceite_declaracao']
        # Perguntas de saúde: Sim/Não obrigatório; o detalhe, quando a
        # resposta é Sim, fica opcional como no formulário original.
        for campo, pergunta, campo_detalhe, pergunta_detalhe in PERGUNTAS_SAUDE:
            self.fields[campo] = forms.ChoiceField(label=pergunta, choices=SIM_NAO, widget=forms.RadioSelect)
            if campo_detalhe:
                self.fields[campo_detalhe] = forms.CharField(
                    label=pergunta_detalhe, required=False, widget=forms.Textarea(attrs={'rows': 2}),
                )
        if 'aceite_declaracao' in self.fields:
            # A declaração fecha a ficha, depois das perguntas de saúde.
            self.fields['aceite_declaracao'] = self.fields.pop('aceite_declaracao')

    def campos_saude(self):
        """Pares (pergunta, detalhe) para o template, na ordem do formulário."""
        return [
            (self[campo], self[campo_detalhe] if campo_detalhe else None)
            for campo, _pergunta, campo_detalhe, _detalhe in PERGUNTAS_SAUDE
        ]

    def clean_nome(self):
        return ' '.join(self.cleaned_data['nome'].split())

    def clean_responsavel_nome(self):
        return ' '.join(self.cleaned_data.get('responsavel_nome', '').split())

    def clean_cpf(self):
        value = self.cleaned_data.get('cpf', '')
        if self.cleaned_data.get('proprio_responsavel') and not cpf_valido(value):
            raise ValidationError('CPF inválido. Confira os 11 números.')
        return digits(value)

    def clean_data_nascimento(self):
        value = self.cleaned_data.get('data_nascimento')
        if value and value > timezone.localdate():
            raise ValidationError('A data de nascimento não pode estar no futuro.')
        return value

    def clean_responsavel_cpf(self):
        if self.cleaned_data.get('proprio_responsavel'):
            return ''
        value = self.cleaned_data.get('responsavel_cpf', '')
        if not cpf_valido(value):
            raise ValidationError('CPF inválido. Confira os 11 números.')
        return digits(value)

    def clean_telefone(self):
        value = self.cleaned_data.get('telefone', '')
        if not 10 <= len(digits(value)) <= 13:
            raise ValidationError('Informe um WhatsApp válido com DDD.')
        return value

    def clean(self):
        data = super().clean()
        nascimento = data.get('data_nascimento')
        if nascimento:
            hoje = timezone.localdate()
            maior = hoje.year - nascimento.year - ((hoje.month, hoje.day) < (nascimento.month, nascimento.day)) >= 18
            if maior:
                data['autorizados_buscar'] = ''
            elif not data.get('autorizados_buscar', '').strip():
                self.add_error('autorizados_buscar', 'Informe quem pode buscar o atleta menor de idade.')
        if data.get('proprio_responsavel'):
            data['responsavel_nome'] = data.get('nome', '')
            data['responsavel_cpf'] = data.get('cpf', '')
        for campo, *_resto in PERGUNTAS_SAUDE:
            if campo in data:
                data[campo] = data[campo] == 'sim'
        return data

    def documentos(self):
        arquivo = self.cleaned_data.get('documento_identidade')
        return [('identidade', arquivo)] if arquivo else []

    def clean_foto(self):
        arquivo = self.cleaned_data.get('foto')
        return preparar_foto(arquivo) if arquivo else None
