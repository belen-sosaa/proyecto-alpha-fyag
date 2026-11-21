from django.contrib.auth.views import LoginView

from apps.cuentas.forms import AccesoRUTForm


class AccesoView(LoginView):
    # Django crea la sesión y gestiona la redirección. El formulario propio resuelve el acceso por RUT.
    template_name = "cuentas/login.html"
    authentication_form = AccesoRUTForm
    extra_context = {"pagina_acceso": True}
