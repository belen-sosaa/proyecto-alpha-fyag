from django.contrib.auth.views import LoginView

from apps.cuentas.forms import AccesoRUTForm


class AccesoView(LoginView):
    template_name = "cuentas/login.html"
    authentication_form = AccesoRUTForm
    extra_context = {"pagina_acceso": True}
