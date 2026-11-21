from django import forms
from django.contrib.auth import authenticate
from django.contrib.auth.hashers import make_password

from apps.bodega.models import PerfilUsuario, normalizar_rut


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
            identidad = (
                PerfilUsuario.objects.filter(rut=rut)
                .values_list("usuario_id", "usuario__username")
                .first()
            )
            if identidad is None:
                make_password(password)
            else:
                usuario_id, username = identidad
                usuario = authenticate(self.request, username=username, password=password)
                if usuario is not None and usuario.pk == usuario_id:
                    self.usuario = usuario
            if self.usuario is None:
                raise forms.ValidationError("RUT o contraseña incorrectos, o cuenta inactiva.")
        return datos

    def get_user(self):
        return self.usuario
