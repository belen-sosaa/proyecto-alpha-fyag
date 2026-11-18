from django import forms
from django.contrib.auth import authenticate
from django.contrib.auth.hashers import make_password

from .models import Articulo, PerfilUsuario, normalizar_rut


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
                # Mantiene un costo de verificación similar para un RUT sin cuenta.
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


class ArticuloForm(forms.ModelForm):
    class Meta:
        model = Articulo
        fields = [
            "sku",
            "codigo_barras",
            "descripcion",
            "categoria",
            "unidad_medida",
            "stock_fisico",
            "stock_minimo",
            "stock_critico",
        ]

    def clean_sku(self):
        sku = self.cleaned_data["sku"].strip()

        existentes = Articulo.objects.filter(sku=sku)
        if self.instance.pk:
            existentes = existentes.exclude(pk=self.instance.pk)

        if existentes.exists():
            raise forms.ValidationError("Artículo ya registrado")

        return sku

    def clean_codigo_barras(self):
        codigo = self.cleaned_data.get("codigo_barras")

        # Los campos vacíos se guardan como NULL.
        codigo = codigo.strip() if codigo else None
        if not codigo:
            return None

        existentes = Articulo.objects.filter(codigo_barras=codigo)
        if self.instance.pk:
            existentes = existentes.exclude(pk=self.instance.pk)

        if existentes.exists():
            raise forms.ValidationError("Artículo ya registrado")

        return codigo
