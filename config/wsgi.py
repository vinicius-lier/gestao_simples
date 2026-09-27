"""
WSGI config for config project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/6.1/howto/deployment/wsgi/
"""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

application = get_wsgi_application()

# Retoma os repasses Pix que ficaram em aberto quando o site parou (deploy,
# reinício). Sem nenhum aberto, só consulta o banco e para.
from integracoes.woovi.repasses import acompanhar_repasses  # noqa: E402

acompanhar_repasses()
