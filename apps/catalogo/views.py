from django.contrib import messages
from django.db import IntegrityError, transaction
from django.shortcuts import redirect, render

from apps.bodega.models import Articulo, BitacoraAuditoria
from apps.cuentas.decorators import facultad_requerida
from apps.catalogo.forms import ArticuloForm


@facultad_requerida("ingresar_articulo")
def articulo_crear(request):
    form = ArticuloForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                articulo = form.save()
                BitacoraAuditoria.objects.create(
                    usuario=request.user, accion="CREAR", entidad="Articulo",
                    id_registro=articulo.pk, valor_anterior=None,
                    valor_nuevo={
                        "sku": articulo.sku, "codigo_barras": articulo.codigo_barras,
                        "descripcion": articulo.descripcion, "categoria": articulo.categoria_id,
                        "unidad_medida": articulo.unidad_medida,
                        "stock_fisico": articulo.stock_fisico,
                        "stock_minimo": articulo.stock_minimo,
                        "stock_critico": articulo.stock_critico,
                    },
                    detalle="Creación de artículo.",
                )
        except IntegrityError:
            duplicado = False
            sku = form.cleaned_data["sku"]
            codigo = form.cleaned_data.get("codigo_barras")
            if Articulo.objects.filter(sku=sku).exists():
                form.add_error("sku", "Artículo ya registrado")
                duplicado = True
            if codigo and Articulo.objects.filter(codigo_barras=codigo).exists():
                form.add_error("codigo_barras", "Artículo ya registrado")
                duplicado = True
            if not duplicado:
                raise
        else:
            messages.success(request, "Artículo creado correctamente.")
            return redirect("bodega_web")
    return render(request, "catalogo/articulo_form.html", {"form": form})
