"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import include, path

from config.views import favicon, health
from integracoes.evolution.views import webhook_conexao as webhook_evolution
from integracoes.woovi.views import webhook as webhook_woovi

urlpatterns = [
    path('favicon.ico', favicon, name='favicon'),
    path('health/', health, name='health'),
    path('', include('portal.urls')),
    path('admin/', admin.site.urls),
    path('webhooks/woovi/', webhook_woovi, name='webhook_woovi'),
    path('webhooks/evolution/', webhook_evolution, name='webhook_evolution'),
    path(
        'webhooks/evolution/<str:instancia>/',
        webhook_evolution,
        name='webhook_evolution_instancia',
    ),
]
