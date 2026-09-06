from django.db import migrations


def organizar(apps, schema_editor):
    Academia = apps.get_model('academias', 'Academia')
    Unidade = apps.get_model('academias', 'Unidade')
    Professor = apps.get_model('servicos', 'Professor')
    Turma = apps.get_model('servicos', 'Turma')
    Matricula = apps.get_model('matriculas', 'Matricula')
    for academia in Academia.objects.all():
        matriz, _ = Unidade.objects.get_or_create(academia_id=academia.pk, nome='Matriz')
        Turma.objects.filter(academia_id=academia.pk, unidade__isnull=True).update(unidade_id=matriz.pk)
        Matricula.objects.filter(academia_id=academia.pk, unidade__isnull=True).update(unidade_id=matriz.pk)
        for turma in Turma.objects.filter(academia_id=academia.pk).exclude(professor=''):
            docente, _ = Professor.objects.get_or_create(academia_id=academia.pk, nome=turma.professor)
            turma.docente_id = docente.pk
            turma.save(update_fields=['docente'])


class Migration(migrations.Migration):
    dependencies = [
        ('portal', '0003_acessoacademia_administrador'),
        ('academias', '0002_unidade'),
        ('servicos', '0002_turma_unidade_professor_turma_docente'),
        ('matriculas', '0002_matricula_unidade'),
    ]
    operations = [migrations.RunPython(organizar, migrations.RunPython.noop)]
