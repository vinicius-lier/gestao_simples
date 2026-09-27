from django.db import models


class AlertaEnviado(models.Model):
    """Último aviso de cada tipo de alerta. Evita mandar ao Discord o mesmo
    aviso a cada ocorrência — vale entre os processos do Gunicorn e as
    tarefas agendadas, que não dividem memória."""

    chave = models.CharField(max_length=200, unique=True)
    ultimo_envio = models.DateTimeField()
    repeticoes = models.PositiveIntegerField(
        default=0, help_text="Ocorrências desde o último aviso, que não geraram aviso novo.",
    )

    class Meta:
        ordering = ["-ultimo_envio"]
        verbose_name = "alerta enviado"
        verbose_name_plural = "alertas enviados"

    def __str__(self):
        return self.chave
