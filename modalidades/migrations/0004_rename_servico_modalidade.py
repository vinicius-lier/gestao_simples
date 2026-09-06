import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('academias', '0001_initial'),
        ('servicos', '0003_alter_servico_options_graduacao_professor_faixa_and_more'),
    ]

    operations = [
        migrations.RenameModel(
            old_name='Servico',
            new_name='Modalidade',
        ),
        migrations.RenameField(
            model_name='turma',
            old_name='servico',
            new_name='modalidade',
        ),
        migrations.AlterField(
            model_name='modalidade',
            name='academia',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='modalidades',
                to='academias.academia',
            ),
        ),
    ]
