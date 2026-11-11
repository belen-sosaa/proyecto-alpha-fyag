from django import forms

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