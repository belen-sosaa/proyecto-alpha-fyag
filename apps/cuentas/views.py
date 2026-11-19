from django.contrib.auth.views import LoginView
from django.urls import reverse

from apps.cuentas.forms import AccesoRUTForm
from apps.cuentas.roles import BODEGUERO, SUPERVISOR, rol_principal


class AccesoView(LoginView):
    template_name = "cuentas/login.html"
    authentication_form = AccesoRUTForm
    extra_context = {"pagina_acceso": True}

    def get_default_redirect_url(self):
        if rol_principal(self.request.user) in (BODEGUERO, SUPERVISOR):
            return reverse("operaciones")
        return super().get_default_redirect_url()
