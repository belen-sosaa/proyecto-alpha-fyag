from django import forms

from apps.bodega.models import Articulo
from apps.catalogo.especificaciones import validar_caracteristicas


class ArticuloForm(forms.ModelForm):
    # El alta desde Bodega incluye el stock inicial junto con la identificación del insumo.
    class Meta:
        model = Articulo
        fields = [
            "sku", "codigo_barras", "descripcion", "categoria", "unidad_medida",
            "ubicacion_fisica", "sector_bodega", "stock_fisico", "stock_minimo", "stock_critico",
        ]

    def clean_sku(self):
        sku = self.cleaned_data["sku"].strip().upper()
        existentes = Articulo.objects.filter(sku__iexact=sku)
        if self.instance.pk:
            existentes = existentes.exclude(pk=self.instance.pk)
        if existentes.exists():
            raise forms.ValidationError("Artículo ya registrado")
        return sku

    def clean_codigo_barras(self):
        # El código leído o escrito se guarda como texto para conservar los ceros iniciales.
        codigo = self.cleaned_data.get("codigo_barras")
        codigo = codigo.strip().upper() if codigo else None
        if not codigo:
            return None
        existentes = Articulo.objects.filter(codigo_barras__iexact=codigo)
        if self.instance.pk:
            # Al editar se excluye el propio artículo, para que pueda conservar su código.
            existentes = existentes.exclude(pk=self.instance.pk)
        if existentes.exists():
            raise forms.ValidationError("Artículo ya registrado")
        return codigo


class ArticuloCatalogoForm(ArticuloForm):
    # Catálogo agrega una ficha opcional de características y deja fuera el stock físico.
    especificaciones = forms.JSONField(
        required=False,
        label="Especificaciones técnicas",
        widget=forms.Textarea(attrs={
            "rows": 6,
            "placeholder": '{"marca": "Ejemplo", "modelo": "ABC", "voltaje": 220}',
        }),
        help_text="Ingresa un objeto JSON con las características técnicas.",
    )

    class Meta(ArticuloForm.Meta):
        fields = [
            "sku", "codigo_barras", "descripcion", "categoria", "unidad_medida",
            "ubicacion_fisica", "sector_bodega", "stock_minimo", "stock_critico",
        ]

    def clean_especificaciones(self):
        datos = self.cleaned_data.get("especificaciones")
        if datos is not None:
            try:
                validar_caracteristicas(datos)
            except ValueError as error:
                raise forms.ValidationError(str(error)) from error
        return datos

    def clean(self):
        datos = super().clean()
        minimo = datos.get("stock_minimo")
        critico = datos.get("stock_critico")
        # El umbral crítico debe quedar dentro del mínimo configurado para el mismo insumo.
        if minimo is not None and critico is not None and critico > minimo:
            self.add_error("stock_critico", "El stock crítico no puede superar al stock mínimo.")
        return datos
