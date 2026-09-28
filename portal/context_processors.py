def alertas_plataforma(request):
    """Para superusuários (equipe da plataforma): quantos repasses às
    academias precisam de intervenção. Ninguém mais vê este aviso."""
    usuario = getattr(request, "user", None)
    if not (usuario and usuario.is_authenticated and usuario.is_superuser):
        return {}
    from financeiro.models import Repasse

    return {"repasses_requer_atencao": Repasse.objects.filter(status=Repasse.REQUER_ATENCAO).count()}


def assinatura_vencida(request):
    """Para o administrador da academia: a faixa no topo do painel quando a
    mensalidade do sistema está atrasada (dentro da tolerância) ou quando a
    assinatura está suspensa. A assinatura já vem com as regras do dia
    aplicadas por ``academia_required``."""
    assinatura = getattr(request, "assinatura_sistema", None)
    if assinatura is None or not getattr(request, "administrador_academia", False):
        return {}
    tela = request.resolver_match.url_name if getattr(request, "resolver_match", None) else ""
    if tela in ("minha_assinatura", "assinatura_pagar"):
        return {}  # essas telas já mostram a situação no próprio conteúdo
    from django.utils import timezone

    from assinaturas.models import FaturaAssinatura

    if assinatura.suspensa:
        return {"aviso_assinatura": {"suspensa": True}}
    fatura = assinatura.faturas.filter(
        status__in=FaturaAssinatura.EM_ABERTO, vencimento__lt=timezone.localdate(),
    ).order_by("vencimento").first()
    return {"aviso_assinatura": {"suspensa": False, "fatura": fatura}} if fatura else {}
