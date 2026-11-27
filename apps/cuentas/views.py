from django.contrib.auth.views import LoginView
from django.shortcuts import render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.csrf import csrf_failure
from django.views.decorators.cache import never_cache

from apps.cuentas.forms import AccesoRUTForm


class AccesoView(LoginView):
    # Django crea la sesión y gestiona la redirección. El formulario propio resuelve el acceso por RUT.
    template_name = "cuentas/login.html"
    authentication_form = AccesoRUTForm


@never_cache
def fallo_csrf(request, reason=""):
    if request.method != "POST" or not request.resolver_match or request.resolver_match.url_name != "login":
        return csrf_failure(request, reason=reason)

    destino = request.POST.get("next", "")
    if not url_has_allowed_host_and_scheme(destino, {request.get_host()}, require_https=request.is_secure()):
        destino = ""
    return render(request, AccesoView.template_name, {
        "error_csrf": True,
        "next": destino,
    }, status=403)
