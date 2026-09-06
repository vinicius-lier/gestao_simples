from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('servicos', '0004_rename_servico_modalidade'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='modalidade',
            name='valor_padrao',
        ),
        migrations.RemoveField(
            model_name='modalidade',
            name='dia_vencimento',
        ),
        migrations.AddField(
            model_name='turma',
            name='valor_mensalidade',
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                help_text='Valor de referência da mensalidade desta turma.',
                max_digits=10,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name='turma',
            name='dia_vencimento',
            field=models.PositiveSmallIntegerField(default=10),
        ),
    ]
