from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from financeiro.models import Mensalidade
from .forms_cobrancas import CobrancaAvulsaForm
from .views import academia_required


@academia_required
@require_http_methods(['GET','POST'])
def nova_cobranca(request):
    if not request.administrador_academia:
        raise PermissionDenied('Somente o administrador pode lançar cobranças avulsas.')
    form = CobrancaAvulsaForm(request.POST or None, academia=request.academia, usuario=request.user)
    if request.method == 'POST' and form.is_valid():
        dados = form.cleaned_data
        with transaction.atomic():
            cobranca, criada = Mensalidade.objects.get_or_create(chave_avulsa=dados['chave'], defaults={
                'academia':request.academia, 'matricula':dados['matricula'], 'tipo':dados['tipo'],
                'descricao_avulsa':dados['descricao'], 'valor':dados['valor'],
                'competencia':dados['vencimento'].replace(day=1), 'vencimento':dados['vencimento'],
                'status':'vencida' if dados['vencimento'] < timezone.localdate() else 'pendente',
            })
            if cobranca.academia_id != request.academia.pk:
                raise PermissionDenied
        messages.success(request, 'Cobrança criada. Use Gerar Pix, Enviar cobrança ou Marcar pago na lista.' if criada else 'Esta cobrança já foi registrada; nenhum lançamento duplicado foi criado.')
        return redirect('portal:financeiro_cobrancas')
    return render(request, 'portal/cobranca_nova.html', {'form':form})
