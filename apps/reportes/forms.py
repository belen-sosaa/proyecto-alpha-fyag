from django import forms

from apps.bodega.models import Categoria, MovimientoCardex
from apps.compras.models import EntidadComercial, FacturaCompra


class RangoFechasForm(forms.Form):
    fecha_desde = forms.DateField(required=False, input_formats=["%Y-%m-%d"])
    fecha_hasta = forms.DateField(required=False, input_formats=["%Y-%m-%d"])

    def clean(self):
        datos = super().clean()
        if datos.get("fecha_desde") and datos.get("fecha_hasta"):
            if datos["fecha_desde"] > datos["fecha_hasta"]:
                raise forms.ValidationError("La fecha desde no puede ser posterior a la fecha hasta.")
        return datos


class ReporteIngresosForm(RangoFechasForm):
    proveedor = forms.ModelChoiceField(
        queryset=EntidadComercial.objects.all().order_by("razon_social"),
        required=False,
    )
    articulo = forms.CharField(required=False, max_length=200)
    tipo_documento = forms.ChoiceField(
        choices=(("", "Todos"), ("FACTURA", "Factura"), ("GUIA_DESPACHO", "Guía"),
                 ("NOTA_CREDITO", "Nota de Crédito"), ("AJUSTE", "Ajuste")),
        required=False,
    )
    estado = forms.ChoiceField(
        choices=(("", "Todos"), ("CONFIRMADO", "Confirmado"),
                 ("PENDIENTE", "Pendiente"), ("HISTORICO", "Histórico")),
        required=False,
    )


class ReporteEgresosForm(RangoFechasForm):
    tipo = forms.ChoiceField(
        choices=(("", "Todos"), ("DESPACHO", "Despacho"), ("MERMA", "Merma"),
                 ("DEVOLUCION", "Devolución"), ("REVERSA", "Reversa"), ("USO_INTERNO", "Uso interno")),
        required=False,
    )
    obra = forms.CharField(required=False, max_length=200)
    proveedor = forms.ModelChoiceField(
        queryset=EntidadComercial.objects.all().order_by("razon_social"),
        required=False,
    )
    articulo = forms.CharField(required=False, max_length=200)


class ReporteInventarioForm(forms.Form):
    categoria = forms.ModelChoiceField(
        queryset=Categoria.objects.all().order_by("nombre"),
        required=False,
    )
    estado_stock = forms.ChoiceField(
        choices=(("", "Todos"), ("normal", "Normal"), ("bajo", "Bajo"),
                 ("critico", "Crítico"), ("sin_stock", "Sin stock")),
        required=False,
    )
    q = forms.CharField(required=False, max_length=200)


class ReporteCardexForm(RangoFechasForm):
    tipo = forms.ChoiceField(choices=[("", "Todos"), *MovimientoCardex.Tipo.choices], required=False)
    articulo = forms.CharField(label="Artículo", required=False, max_length=200)
    obra = forms.CharField(required=False, max_length=200)
    usuario = forms.CharField(required=False, max_length=150)
