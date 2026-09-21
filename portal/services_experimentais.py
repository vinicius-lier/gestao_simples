from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from .models import AulaExperimental, ConfiguracaoExperimental, InscricaoExperimental


def bloquear_aula(pk):
    # A escrita inicial serializa também SQLite, onde select_for_update é inoperante.
    AulaExperimental.objects.filter(pk=pk).update(vagas=F('vagas'))
    return AulaExperimental.objects.select_related('turma__academia', 'turma__unidade', 'turma__modalidade').get(pk=pk)


@transaction.atomic
def inscrever(pk, dados, aceitar_fila=False):
    aula = bloquear_aula(pk)
    config = ConfiguracaoExperimental.resolver(aula.turma.academia)
    if not config.ativo:
        raise ValidationError('Os agendamentos pelo site estão temporariamente suspensos.')
    if not aula.aberta:
        raise ValidationError('As inscrições para este horário estão encerradas.')
    if aula.inicio > config.limite_janela():
        raise ValidationError('Este horário ainda não está aberto para agendamento.')
    if aula.inscricoes.filter(nome__iexact=dados['nome'], telefone=dados['telefone']).exclude(status='cancelado').exists():
        raise ValidationError('Já existe uma inscrição para este participante e contato neste horário.')
    # Quem já está na fila tem prioridade sobre novas inscrições.
    lotada = aula.vagas_disponiveis == 0 or aula.inscricoes.filter(status='espera').exists()
    if lotada and not (aula.fila_habilitada and aceitar_fila):
        raise ValidationError('Não há vaga disponível. Se a fila estiver habilitada, marque a opção para entrar na fila de espera ou escolha outro horário.')
    return InscricaoExperimental.objects.create(aula=aula, status='espera' if lotada else 'confirmado', **dados)


@transaction.atomic
def alterar_status(inscricao, status):
    aula = bloquear_aula(inscricao.aula_id)
    inscricao.refresh_from_db()
    permitidos = {'espera': {'confirmado', 'cancelado'}, 'confirmado': {'cancelado', 'presente', 'faltou', 'convertido'},
                 'presente': {'convertido'}, 'faltou': set(), 'convertido': set(), 'cancelado': set()}
    if status not in permitidos.get(inscricao.status, set()):
        raise ValidationError('Transição de status inválida.')
    if status == 'confirmado':
        if not aula.ativa or aula.fim <= timezone.now() or not aula.vagas_disponiveis:
            raise ValidationError('Não é possível confirmar: aula indisponível ou sem vagas.')
        if aula.inscricoes.filter(status='espera').first().pk != inscricao.pk:
            raise ValidationError('Confirme primeiro o participante mais antigo da fila.')
    if status in {'presente', 'faltou'} and aula.inicio > timezone.now():
        raise ValidationError('Registre presença ou falta somente após o início da aula.')
    inscricao.status = status
    inscricao.save(update_fields=['status'])
