def alertas_plataforma(request):
    """Para superusuários (equipe da plataforma): quantos repasses às
    academias precisam de intervenção. Ninguém mais vê este aviso."""
    usuario = getattr(request, "user", None)
    if not (usuario and usuario.is_authenticated and usuario.is_superuser):
        return {}
    from financeiro.models import Repasse

    return {"repasses_requer_atencao": Repasse.objects.filter(status=Repasse.REQUER_ATENCAO).count()}
