from django.apps import AppConfig


class ModalidadesConfig(AppConfig):
    name = 'modalidades'
    # Mantém o app_label histórico para não reescrever migrations antigas
    # nem as referências 'servicos.*' feitas por outros apps.
    label = 'servicos'
    verbose_name = 'Modalidades'
