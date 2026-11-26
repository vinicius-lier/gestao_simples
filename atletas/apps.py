from django.apps import AppConfig


class AtletasConfig(AppConfig):
    name = 'atletas'

    def ready(self):
        from . import checks, signals  # noqa: F401
