from datetime import timedelta

from django import forms
from django.contrib.auth import authenticate
from django.contrib.auth.hashers import make_password
from django.db import transaction
from django.utils import timezone

from apps.bodega.models import PerfilUsuario, normalizar_rut


MAX_INTENTOS_FALLIDOS = 5
DURACION_BLOQUEO = timedelta(minutes=15)


@transaction.atomic
def autenticar_rut(request, rut, password):
    perfil = (
        PerfilUsuario.objects.select_related("usuario")
        .select_for_update(of=("self",))
        .filter(rut=rut)
        .first()
    )
    if perfil is None or (
        perfil.bloqueado_hasta and perfil.bloqueado_hasta > timezone.now()
    ):
        make_password(password)
        return None

    usuario = authenticate(request, username=perfil.usuario.get_username(), password=password)
    if usuario is not None and usuario.pk == perfil.usuario_id:
        if perfil.intentos_fallidos or perfil.bloqueado_hasta:
            perfil.intentos_fallidos = 0
            perfil.bloqueado_hasta = None
            perfil.save(update_fields=["intentos_fallidos", "bloqueado_hasta"])
        return usuario

    # Al vencer el bloqueo, el primer fallo comienza un nuevo conteo.
    intentos_previos = 0 if perfil.bloqueado_hasta else perfil.intentos_fallidos
    perfil.intentos_fallidos = intentos_previos + 1
    perfil.bloqueado_hasta = (
        timezone.now() + DURACION_BLOQUEO
        if perfil.intentos_fallidos >= MAX_INTENTOS_FALLIDOS
        else None
    )
    perfil.save(update_fields=["intentos_fallidos", "bloqueado_hasta"])
    return None


class AccesoRUTForm(forms.Form):
    rut = forms.CharField(label="RUT", max_length=12)
    password = forms.CharField(label="Contraseña", strip=False, widget=forms.PasswordInput)

    def __init__(self, request=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.request = request
        self.usuario = None

    def clean_rut(self):
        return normalizar_rut(self.cleaned_data["rut"])

    def clean(self):
        datos = super().clean()
        rut = datos.get("rut")
        password = datos.get("password")
        if rut and password:
            self.usuario = autenticar_rut(self.request, rut, password)
            if self.usuario is None:
                raise forms.ValidationError("RUT o contraseña incorrectos, o cuenta inactiva.")
        return datos

    def get_user(self):
        return self.usuario
