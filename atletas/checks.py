import os

from django.conf import settings
from django.core.checks import Warning, register


@register()
def volume_dos_arquivos(app_configs, **kwargs):
    """Sem o volume persistente, foto e documentos dos alunos somem no
    próximo deploy. Aparece no log do ``migrate`` de cada deploy."""
    if settings.DEBUG or not getattr(settings, 'MEDIA_EXIGE_VOLUME', True):
        return []
    raiz = str(settings.MEDIA_ROOT)
    if os.path.ismount(raiz):
        return []
    return [Warning(
        f'MEDIA_ROOT ({raiz}) não é um volume montado: fotos e documentos dos alunos '
        'serão apagados no próximo deploy.',
        hint='No Coolify: Persistent Storage → Volume Mount com destino /app/media.',
        id='arquivos.W001',
    )]
