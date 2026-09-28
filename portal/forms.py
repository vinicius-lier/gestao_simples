import unicodedata

from django import forms
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from academias.models import Academia, Unidade
from atletas.models import Atleta, Responsavel
from matriculas.models import Matricula
from modalidades.models import Modalidade, Turma, Professor, Graduacao


def digits(value):
    return ''.join(c for c in value if c.isdigit())


def normalizar(texto):
    """Sem acento, minúsculo e com espaços simples — para comparar nomes."""
    sem_acento = unicodedata.normalize('NFKD', texto or '').encode('ascii', 'ignore').decode()
    return ' '.join(sem_acento.casefold().split())


def responsavel_divergente(aluno):
    """O responsável ligado ao aluno quando o aluno está marcado como o
    próprio responsável, mas o cadastro é de outro nome; senão None."""
    responsavel = aluno.responsavel_financeiro
    if (aluno.proprio_responsavel and responsavel is not None
            and responsavel.academia_id == aluno.academia_id
            and normalizar(responsavel.nome) != normalizar(aluno.nome)):
        return responsavel
    return None


def _nao_negativo(valor):
    if valor is not None and valor < 0:
        raise ValidationError('O valor não pode ser negativo.')
    return valor


def cpf_valido(valor):
    """Checagem propositalmente simples: exige 11 dígitos e rejeita
    sequências óbvias como '11111111111'. Não valida o dígito
    verificador — o projeto usa CPFs de teste sem esse cuidado (ex.:
    '12345678900'), e o problema real que motivou este checador foi um
    CPF com 12 dígitos passando sem aviso e só quebrando depois, ao
    gerar a cobrança no gateway de pagamento."""
    numeros = digits(valor)
    return len(numeros) == 11 and numeros != numeros[0] * 11


class AlunoForm(forms.ModelForm):
    proprio_responsavel = forms.BooleanField(required=False, label='O próprio aluno é o responsável financeiro')
    aluno_email = forms.EmailField(required=False, label='E-mail do aluno')
    responsavel = forms.ModelChoiceField(queryset=Responsavel.objects.none(), required=False, label='Responsável existente')
    responsavel_nome = forms.CharField(max_length=150, required=False, label='Nome do novo responsável')
    responsavel_cpf = forms.CharField(max_length=14, required=False, label='CPF do novo responsável')
    responsavel_whatsapp = forms.CharField(max_length=20, required=False, label='WhatsApp do novo responsável')
    responsavel_email = forms.EmailField(required=False, label='E-mail do novo responsável')

    class Meta:
        model = Atleta
        fields = ('nome', 'data_nascimento', 'cpf', 'telefone', 'faixa', 'status', 'observacoes', 'proprio_responsavel')
        help_texts = {'telefone': 'Com DDD. Se o aluno for o próprio responsável financeiro, as cobranças vão para este WhatsApp.'}
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
                    if not self.instance.telefone:
                        # Cadastro antigo: o WhatsApp estava só no responsável.
                        self.initial['telefone'] = r.whatsapp
                    self.initial['aluno_email'] = r.email
            else:
                self.initial['responsavel'] = self.instance.responsavel_financeiro_id
        # Marcado como o próprio responsável, mas ligado ao cadastro de outro
        # nome (vínculo feito pelo CPF antes da checagem de nome): o
        # formulário avisa o que o salvamento vai mudar.
        self.responsavel_divergente = responsavel_divergente(self.instance) if self.instance.pk else None
        self.alunos_do_responsavel = (
            list(self.responsavel_divergente.atletas.exclude(pk=self.instance.pk).values_list('nome', flat=True))
            if self.responsavel_divergente else []
        )
        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-check-input' if isinstance(field.widget, forms.CheckboxInput) else 'form-select' if isinstance(field.widget, forms.Select) else 'form-control'

    def clean_data_nascimento(self):
        value = self.cleaned_data['data_nascimento']
        if value and value > timezone.localdate():
            raise ValidationError('O nascimento não pode estar no futuro.')
        return value

    def clean_cpf(self):
        value = self.cleaned_data.get('cpf', '')
        if value and not cpf_valido(value):
            raise ValidationError('CPF inválido. Confira os números digitados.')
        return value

    def clean_telefone(self):
        value = digits(self.cleaned_data.get('telefone', ''))
        if value and not 10 <= len(value) <= 13:
            raise ValidationError('Informe um telefone válido com DDD.')
        return value

    def clean_responsavel_cpf(self):
        value = self.cleaned_data.get('responsavel_cpf', '')
        if value and not cpf_valido(value):
            raise ValidationError('CPF inválido. Confira os números digitados.')
        return value

    def clean_responsavel_whatsapp(self):
        value = self.cleaned_data['responsavel_whatsapp']
        if value and not digits(value):
            raise ValidationError('Informe um telefone com números.')
        return value

    def _responsavel_com_cpf(self, cpf):
        """Outro responsável da academia com este CPF (não o atual do aluno)."""
        atual = self.instance.responsavel_financeiro_id if self.instance.pk else None
        return next(
            (r for r in Responsavel.objects.filter(academia=self.academia).exclude(pk=atual)
             if digits(r.cpf) == cpf),
            None,
        )

    def clean(self):
        data = super().clean()
        if data.get('proprio_responsavel'):
            cpf = digits(data.get('cpf', ''))
            if not self.errors.get('cpf') and not cpf:
                self.add_error('cpf', 'Informe o CPF do aluno responsável financeiro.')
            if not self.errors.get('telefone') and not data.get('telefone'):
                self.add_error('telefone', 'Informe o WhatsApp do aluno: ele recebe as cobranças.')
            if cpf and not self.errors.get('cpf'):
                # O aluno é o próprio responsável: um responsável com este CPF
                # só pode ser ele mesmo. Com outro nome, é CPF digitado errado
                # ou cadastro de outra pessoa — nunca vincular em silêncio.
                existente = self._responsavel_com_cpf(cpf)
                if existente is not None and normalizar(existente.nome) != normalizar(data.get('nome', '')):
                    self.add_error('cpf', (
                        f'Este CPF já está no cadastro do responsável "{existente.nome}". '
                        'Confira o CPF; se for a mesma pessoa, corrija o nome desse cadastro antes.'
                    ))
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
                self.cleaned_data['responsavel_whatsapp'] = self.cleaned_data['telefone']
                self.cleaned_data['responsavel_email'] = self.cleaned_data['aluno_email']
            responsavel = self.cleaned_data.get('responsavel')
            atual = self.instance.responsavel_financeiro if self.instance.pk else None
            if (self.cleaned_data.get('proprio_responsavel') and self.initial.get('proprio_responsavel')
                    and atual is not None and atual.academia_id == self.academia.pk):
                # Editando um aluno que já era o próprio responsável: o que foi
                # digitado são os dados atuais dele. (Só no cadastro de aluno
                # novo um responsável achado pelo CPF mantém o contato antigo.)
                atual.nome = self.cleaned_data['responsavel_nome'].strip()
                atual.cpf = digits(self.cleaned_data.get('responsavel_cpf', ''))
                atual.whatsapp = digits(self.cleaned_data['responsavel_whatsapp'])
                atual.email = self.cleaned_data.get('responsavel_email', '')
                atual.save(update_fields=['nome', 'cpf', 'whatsapp', 'email'])
                responsavel = atual
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


class TurmaSelect(forms.Select):
    """Anota cada <option> de turma com dados para o filtro/preenchimento no navegador."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.turmas = {}

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        turma = self.turmas.get(str(getattr(value, 'value', value)))
        if turma is not None:
            option['attrs'].update({
                'data-modalidade': turma.modalidade_id,
                'data-unidade': turma.unidade_id or '',
                'data-valor': '' if turma.valor_mensalidade is None else turma.valor_mensalidade,
                'data-valor-apos': '' if turma.valor_apos_vencimento is None else turma.valor_apos_vencimento,
                'data-taxa': '' if turma.taxa_matricula is None else turma.taxa_matricula,
                'data-vencimento': turma.dia_vencimento,
            })
        return option


class MatriculaForm(forms.ModelForm):
    class Meta:
        model = Matricula
        fields = ('unidade', 'modalidade', 'turma', 'valor_mensalidade', 'valor_apos_vencimento', 'taxa_matricula', 'dia_vencimento', 'primeiro_vencimento', 'data_inicio', 'data_fim', 'ativo')
        labels = {'valor_apos_vencimento': 'Valor após o vencimento (R$)', 'taxa_matricula': 'Taxa de matrícula (R$)'}
        widgets = {key: forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}) for key in ('primeiro_vencimento', 'data_inicio', 'data_fim')}
        widgets['turma'] = TurmaSelect

    def __init__(self, *args, academia, **kwargs):
        super().__init__(*args, **kwargs)
        # Antes da validação, que altera a instância: a cobrança começa
        # quando a matrícula passa de inativa (ou nova) para ativa.
        self.ativa_antes = bool(self.instance.pk and self.instance.ativo)
        self.instance.academia = academia
        self.fields['primeiro_vencimento'].help_text = (
            'Em branco: o próximo dia de vencimento a partir de hoje (ou da data de início, se for futura). '
            'As mensalidades seguintes vencem no dia de vencimento.'
        )
        if self.ativa_antes:
            # A cobrança já começou: as mensalidades se ajustam pela lista de cobranças.
            self.fields['primeiro_vencimento'].disabled = True
            self.fields['primeiro_vencimento'].help_text = 'A cobrança desta matrícula já começou.'
        self.fields['unidade'].queryset = Unidade.objects.filter(academia=academia)
        self.fields['unidade'].required = self.fields['unidade'].queryset.exists()
        self.fields['modalidade'].queryset = Modalidade.objects.filter(academia=academia)
        turmas_qs = Turma.objects.filter(academia=academia, modalidade__academia=academia).select_related('modalidade', 'unidade')
        self.fields['turma'].queryset = turmas_qs
        self.fields['turma'].widget.turmas = {str(t.pk): t for t in turmas_qs}
        # Valor e vencimento vêm da turma; ficam editáveis para exceções (bolsa, desconto).
        self.fields['valor_mensalidade'].required = False
        self.fields['dia_vencimento'].required = False
        self.fields['valor_mensalidade'].help_text = 'Em branco: usa o valor da turma.'
        self.fields['dia_vencimento'].help_text = 'Em branco: usa o vencimento da turma.'
        self.fields['valor_apos_vencimento'].help_text = (
            'Cobrado a partir do dia seguinte ao vencimento. Em branco: usa o da turma. '
            'Vale para as mensalidades criadas daqui em diante.'
        )
        self.fields['taxa_matricula'].help_text = (
            'Gerada uma vez, quando a matrícula é ativada. Em branco: usa a da turma; 0 para não cobrar.'
        )
        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-check-input' if isinstance(field.widget, forms.CheckboxInput) else 'form-select' if isinstance(field.widget, forms.Select) else 'form-control'

    def clean_valor_mensalidade(self):
        value = self.cleaned_data.get('valor_mensalidade')
        if value is not None and value < 0:
            raise ValidationError('O valor não pode ser negativo.')
        return value

    def clean_valor_apos_vencimento(self):
        return _nao_negativo(self.cleaned_data.get('valor_apos_vencimento'))

    def clean_taxa_matricula(self):
        return _nao_negativo(self.cleaned_data.get('taxa_matricula'))

    def clean(self):
        data = super().clean()
        turma = data.get('turma')
        if data.get('valor_mensalidade') is None:
            if turma and turma.valor_mensalidade is not None:
                data['valor_mensalidade'] = turma.valor_mensalidade
            else:
                self.add_error('valor_mensalidade', 'Informe o valor ou escolha uma turma com valor definido.')
        for campo in ('valor_apos_vencimento', 'taxa_matricula'):
            if data.get(campo) is None and turma is not None:
                data[campo] = getattr(turma, campo)
        if not data.get('dia_vencimento'):
            data['dia_vencimento'] = turma.dia_vencimento if turma else 10
        primeiro = data.get('primeiro_vencimento')
        ativando = data.get('ativo') and not self.ativa_antes
        if primeiro and primeiro < timezone.localdate() and (ativando or 'primeiro_vencimento' in self.changed_data):
            self.add_error(
                'primeiro_vencimento',
                'O primeiro vencimento não pode ser anterior a hoje. Deixe em branco para usar o próximo vencimento.',
            )
        return data


class CadastroAcademiaForm(forms.ModelForm):
    def __init__(self, *args, academia, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.academia = academia
        for name, field in self.fields.items():
            field.widget.attrs['class'] = 'form-check-input' if isinstance(field.widget, forms.CheckboxInput) else 'form-select' if isinstance(field.widget, forms.Select) else 'form-control'
        for name, model in [('unidade', Unidade), ('modalidade', Modalidade), ('docente', Professor)]:
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


class TurmaForm(CadastroAcademiaForm):
    class Meta:
        model = Turma
        fields = ('nome', 'unidade', 'modalidade', 'docente', 'dias_semana', 'horario', 'local', 'valor_mensalidade', 'valor_apos_vencimento', 'taxa_matricula', 'dia_vencimento', 'ativo')
        labels = {
            'horario': 'Horário',
            'dias_semana': 'Dias da semana',
            'valor_mensalidade': 'Valor da mensalidade (R$)',
            'valor_apos_vencimento': 'Valor após o vencimento (R$)',
            'taxa_matricula': 'Taxa de matrícula (R$)',
            'dia_vencimento': 'Dia de vencimento',
        }
        widgets = {'horario': forms.TimeInput(format='%H:%M', attrs={'type': 'time'})}

    def __init__(self, *args, academia, **kwargs):
        super().__init__(*args, academia=academia, **kwargs)
        self.fields['valor_mensalidade'].required = False
        self.fields['dia_vencimento'].required = False

    def clean_valor_mensalidade(self):
        valor = self.cleaned_data.get('valor_mensalidade')
        if valor is not None and valor < 0:
            raise ValidationError('O valor não pode ser negativo.')
        return valor

    def clean_valor_apos_vencimento(self):
        return _nao_negativo(self.cleaned_data.get('valor_apos_vencimento'))

    def clean_taxa_matricula(self):
        return _nao_negativo(self.cleaned_data.get('taxa_matricula'))

    def clean_dia_vencimento(self):
        dia = self.cleaned_data.get('dia_vencimento') or 10
        if not 1 <= dia <= 31:
            raise ValidationError('Informe um dia entre 1 e 31.')
        return dia


class ModalidadeForm(CadastroAcademiaForm):
    class Meta:
        model = Modalidade
        fields = ('nome', 'descricao', 'ativo')
        labels = {'descricao': 'Descrição'}
        help_texts = {'nome': 'Ex.: Judô, Jiu-jítsu, Muay Thai, Natação. Valores e horários ficam nas turmas.'}


class GraduacaoInlineForm(forms.ModelForm):
    class Meta:
        model = Graduacao
        fields = ('nome', 'ordem', 'ativo')
        labels = {'nome': 'Faixa / graduação', 'ordem': 'Ordem'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['ordem'].required = False
        self.fields['ordem'].initial = None
        self.fields['ativo'].initial = True

    def has_changed(self):
        # Linha nova só conta se o nome foi preenchido; a linha em branco do fim é ignorada.
        if self.instance.pk:
            return super().has_changed()
        return bool((self.data.get(self.add_prefix('nome')) or '').strip())

    def clean_ordem(self):
        return self.cleaned_data.get('ordem') or 1


class BaseGraduacaoFormSet(forms.BaseInlineFormSet):
    """Formset das faixas exibido dentro do cadastro da modalidade."""

    def __init__(self, *args, academia=None, **kwargs):
        self.academia = academia
        super().__init__(*args, **kwargs)

    def add_fields(self, form, index):
        super().add_fields(form, index)
        for field in form.fields.values():
            field.widget.attrs['class'] = 'form-check-input' if isinstance(field.widget, forms.CheckboxInput) else 'form-control'

    def save(self, commit=True):
        for form in self.forms:
            if form.instance.pk is None:
                form.instance.academia = self.academia
        return super().save(commit=commit)


GraduacaoFormSet = forms.inlineformset_factory(
    Modalidade,
    Graduacao,
    form=GraduacaoInlineForm,
    formset=BaseGraduacaoFormSet,
    extra=1,
    can_delete=True,
)


class ChavePixForm(forms.Form):
    """Chave Pix onde a academia recebe as mensalidades."""

    tipo_chave = forms.ChoiceField(label='Tipo da chave')
    chave = forms.CharField(label='Chave Pix', max_length=100)
    confirmacao = forms.BooleanField(
        label='Confirmo que esta chave Pix é da conta onde a escola deve receber as mensalidades.',
    )

    def __init__(self, *args, **kwargs):
        from financeiro.models import ContaRecebimento

        super().__init__(*args, **kwargs)
        self.fields['tipo_chave'].choices = ContaRecebimento.TIPOS_CHAVE
        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-check-input' if isinstance(field.widget, forms.CheckboxInput) else 'form-select' if isinstance(field.widget, forms.Select) else 'form-control'

    def clean(self):
        from integracoes.woovi.chave_pix import ChavePixInvalida, normalizar_chave_pix

        dados = super().clean()
        if dados.get('tipo_chave') and dados.get('chave'):
            try:
                dados['chave'] = normalizar_chave_pix(dados['tipo_chave'], dados['chave'])
            except ChavePixInvalida as erro:
                self.add_error('chave', str(erro))
        return dados
