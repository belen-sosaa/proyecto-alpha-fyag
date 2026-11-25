from decimal import Decimal

from django import forms

from .models import (
    Articulo,
    DevolucionProveedor,
    FacturaCompra,
    Merma,
    Proveedor,
)


class MermaForm(forms.ModelForm):
    class Meta:
        model = Merma
        fields = ["articulo", "cantidad", "motivo"]

    def clean_cantidad(self):
        cantidad = self.cleaned_data["cantidad"]
        if cantidad <= 0:
            raise forms.ValidationError("La cantidad debe ser mayor que cero.")
        return cantidad

    def clean_motivo(self):
        motivo = self.cleaned_data.get("motivo", "").strip()
        if not motivo:
            raise forms.ValidationError("La justificación de la merma es obligatoria.")
        return motivo

    def clean(self):
        datos = super().clean()
        articulo = datos.get("articulo")
        cantidad = datos.get("cantidad")
        if articulo and cantidad is not None and cantidad > articulo.stock_fisico:
            self.add_error("cantidad", "La cantidad supera el stock disponible.")
        return datos


class CompraFacturaForm(forms.ModelForm):
    articulo = forms.ModelChoiceField(queryset=Articulo.objects.all())
    cantidad = forms.IntegerField(min_value=1)
    precio_unitario = forms.DecimalField(min_value=Decimal("0"), max_digits=12, decimal_places=2)
    descuento_1 = forms.DecimalField(required=False, min_value=Decimal("0"), max_digits=5, decimal_places=2, initial=0)
    descuento_2 = forms.DecimalField(required=False, min_value=Decimal("0"), max_digits=5, decimal_places=2, initial=0)
    descuento_3 = forms.DecimalField(required=False, min_value=Decimal("0"), max_digits=5, decimal_places=2, initial=0)

    class Meta:
        model = FacturaCompra
        fields = ["numero_factura", "proveedor"]

    def clean(self):
        datos = super().clean()
        for campo in ("descuento_1", "descuento_2", "descuento_3"):
            descuento = datos.get(campo)
            if descuento in (None, ""):
                datos[campo] = Decimal("0")
                continue
            if descuento is not None and descuento > Decimal("100"):
                self.add_error(campo, "El descuento no puede superar el 100%.")
        return datos


class DevolucionProveedorForm(forms.ModelForm):
    class Meta:
        model = DevolucionProveedor
        fields = ["proveedor", "articulo", "cantidad", "motivo"]

    def clean_cantidad(self):
        cantidad = self.cleaned_data["cantidad"]
        if cantidad <= 0:
            raise forms.ValidationError("La cantidad debe ser mayor que cero.")
        return cantidad

    def clean_motivo(self):
        motivo = self.cleaned_data.get("motivo", "").strip()
        if not motivo:
            raise forms.ValidationError("El motivo es obligatorio.")
        return motivo

    def clean(self):
        datos = super().clean()
        articulo = datos.get("articulo")
        cantidad = datos.get("cantidad")
        if articulo and cantidad is not None and cantidad > articulo.stock_fisico:
            self.add_error("cantidad", "La cantidad supera el stock disponible.")
        return datos
