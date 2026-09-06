from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('financeiro', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='mensalidade',
            name='forma_pagamento',
            field=models.CharField(
                blank=True,
                choices=[
                    ('pix', 'Pix'),
                    ('boleto', 'Boleto'),
                    ('cartao', 'Cartão'),
                    ('dinheiro', 'Dinheiro'),
                    ('outro', 'Outro'),
                ],
                max_length=20,
            ),
        ),
    ]
