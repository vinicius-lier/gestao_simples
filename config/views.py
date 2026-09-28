import logging

from django.db import DatabaseError, connection
from django.http import HttpResponsePermanentRedirect, JsonResponse
from django.templatetags.static import static
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

logger = logging.getLogger(__name__)


@require_http_methods(["GET", "HEAD"])
def favicon(request):
    """Ícone da aba: o logo da Escola de Judô Keiko Fukuda. Os layouts do
    portal já apontam para ele; esta rota cobre o pedido automático do
    navegador a /favicon.ico nas páginas sem <link rel="icon"> (admin,
    páginas de erro, /health/)."""
    return HttpResponsePermanentRedirect(static("portal/logo-fukuda.png"))


@never_cache
@require_http_methods(["GET", "HEAD"])
def health(request):
    """Health check do contêiner e do Coolify: o processo responde e o banco
    aceita uma consulta. Não chama serviços externos (Woovi, WhatsApp) — uma
    queda deles não pode derrubar a aplicação. Sem login; o Host precisa
    estar em ALLOWED_HOSTS."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except DatabaseError as exc:
        logger.error("Health check: banco indisponível: %s", exc)
        return JsonResponse({"status": "erro", "database": "indisponivel"}, status=503)
    return JsonResponse({"status": "ok", "database": "ok"})
