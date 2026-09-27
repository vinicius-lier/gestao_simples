def alertas_plataforma(request):
    """Para superusuários (equipe da plataforma): quantos repasses às
    academias precisam de intervenção. Ninguém mais vê este aviso."""
    usuario = getattr(request, "user", None)
    if not (usuario and usuario.is_authenticated and usuario.is_superuser):
        return {}
    from financeiro.models import Repasse

    return {"repasses_requer_atencao": Repasse.objects.filter(status=Repasse.REQUER_ATENCAO).count()}


def assinatura_vencida(request):
    """Para o administrador da academia: a fatura do sistema vencida e ainda
    não informada como paga, para a faixa de aviso no topo do portal."""
    academia = getattr(request, "academia", None)
    if academia is None or not getattr(request, "administrador_academia", False):
        return {}
    from django.utils import timezone

    from assinaturas.models import FaturaAssinatura

    fatura = FaturaAssinatura.objects.filter(
        assinatura__academia=academia, status=FaturaAssinatura.ABERTA,
        vencimento__lt=timezone.localdate(), pagamento_informado_em__isnull=True,
    ).order_by("vencimento").first()
    return {"fatura_assinatura_vencida": fatura} if fatura else {}
