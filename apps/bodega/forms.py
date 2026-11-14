from django import forms

from .especificaciones import validar_caracteristicas
from .models import Articulo


class ArticuloForm(forms.ModelForm):
    class Meta:
        model = Articulo
        fields = [
            "sku",
            "codigo_barras",
            "descripcion",
            "categoria",
            "unidad_medida",
            "ubicacion_fisica",
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

        # Los códigos vacíos se almacenan como NULL.
        codigo = codigo.strip() if codigo else None

        if not codigo:
            return None

        existentes = Articulo.objects.filter(codigo_barras=codigo)

        if self.instance.pk:
            existentes = existentes.exclude(pk=self.instance.pk)

        if existentes.exists():
            raise forms.ValidationError("Artículo ya registrado")

        return codigo


class ArticuloCatalogoForm(ArticuloForm):
    especificaciones = forms.JSONField(
        required=False,
        label="Especificaciones técnicas",
        widget=forms.Textarea(
            attrs={
                "rows": 6,
                "placeholder": (
                    '{"marca": "Ejemplo", '
                    '"modelo": "ABC", "voltaje": 220}'
                ),
            }
        ),
        help_text=(
            "Ingresa un objeto JSON con las características técnicas. "
            "Este campo se almacenará en MongoDB."
        ),
    )

    class Meta(ArticuloForm.Meta):
        fields = [
            "sku",
            "codigo_barras",
            "descripcion",
            "categoria",
            "unidad_medida",
            "ubicacion_fisica",
            "stock_minimo",
            "stock_critico",
        ]

    def clean_especificaciones(self):
        datos = self.cleaned_data.get("especificaciones")

        if datos is None:
            return None

        try:
            validar_caracteristicas(datos)
        except ValueError as error:
            raise forms.ValidationError(str(error)) from error

        return datos

    def clean(self):
        datos = super().clean()

        minimo = datos.get("stock_minimo")
        critico = datos.get("stock_critico")

        if minimo is not None and critico is not None:
            if critico > minimo:
                self.add_error(
                    "stock_critico",
                    "El stock crítico no puede superar al stock mínimo.",
                )

        return datos