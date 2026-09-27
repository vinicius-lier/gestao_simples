from django.apps import AppConfig


class PortalConfig(AppConfig):
    name = "portal"

    def ready(self):
        # O Django 6.1 trocou o "---------" da opção vazia dos selects por
        # "- Select an option -", que ainda não tem tradução para pt-BR.
        # O próprio Django orienta redefinir o texto no ready() de uma app;
        # vale para todos os selects, inclusive os do admin.
        from django.db.models import fields

        fields.BLANK_CHOICE_LABEL = "- Selecione uma opção -"
