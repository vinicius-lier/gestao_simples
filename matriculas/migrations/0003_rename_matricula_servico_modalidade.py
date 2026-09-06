from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('matriculas', '0002_matricula_unidade'),
        ('servicos', '0004_rename_servico_modalidade'),
    ]

    operations = [
        migrations.RenameField(
            model_name='matricula',
            old_name='servico',
            new_name='modalidade',
        ),
    ]
