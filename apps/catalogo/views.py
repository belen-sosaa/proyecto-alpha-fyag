from decimal import Decimal

from django.contrib import messages
from django.db import IntegrityError, connection, transaction
from django.db.models import Q
from django.db.models.deletion import ProtectedError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.contrib.auth.decorators import login_required
from openpyxl import Workbook

from apps.bodega.models import (
    Articulo,
    BitacoraAuditoria,
    Categoria,
    Despacho,
    DetalleFacturaCompra,
)
from apps.catalogo.especificaciones import guardar_especificaciones
from apps.catalogo.forms import ArticuloCatalogoForm, ArticuloForm
from apps.cuentas.decorators import facultad_requerida
from apps.cuentas.roles import permisos_de


def datos_articulo(articulo):
    """Serializa los datos relacionales del insumo para la auditoría."""
    return {
        "sku": articulo.sku,
        "codigo_barras": articulo.codigo_barras,
        "descripcion": articulo.descripcion,
        "categoria": articulo.categoria_id,
        "unidad_medida": articulo.unidad_medida,
        "ubicacion_fisica": articulo.ubicacion_fisica,
        "stock_fisico": articulo.stock_fisico,
        "stock_minimo": articulo.stock_minimo,
        "stock_critico": articulo.stock_critico,
    }


def _guardar_especificaciones(articulo, datos):
    if datos:
        identificador = guardar_especificaciones(articulo.pk, datos)
        Articulo.objects.filter(pk=articulo.pk).update(
            mongodb_especificaciones_id=str(identificador)
        )


@facultad_requerida("ver_catalogo")
def catalogo_listar(request):
    busqueda = request.GET.get("q", "").strip()
    categoria_id = request.GET.get("categoria", "").strip()
    articulos = Articulo.objects.select_related("categoria").order_by("sku")
    if busqueda:
        articulos = articulos.filter(
            Q(sku__icontains=busqueda)
            | Q(descripcion__icontains=busqueda)
            | Q(codigo_barras__icontains=busqueda)
            | Q(ubicacion_fisica__icontains=busqueda)
        )
    if categoria_id:
        articulos = (
            articulos.filter(categoria_id=int(categoria_id))
            if categoria_id.isdecimal() else articulos.none()
        )
    return render(request, "catalogo/catalogo.html", {
        "articulos": articulos,
        "categorias": Categoria.objects.order_by("nombre"),
        "q": busqueda,
        "categoria_seleccionada": categoria_id,
        "perm": permisos_de(request.user),
    })


@facultad_requerida("ingresar_articulo")
def articulo_crear(request):
    """Alta usada desde el panel de bodega, con ingreso de stock inicial."""
    form = ArticuloForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                articulo = form.save()
                BitacoraAuditoria.objects.create(
                    usuario=request.user, accion="CREAR", entidad="Articulo",
                    id_registro=articulo.pk, valor_anterior=None,
                    valor_nuevo=datos_articulo(articulo),
                    detalle="Creación de artículo.",
                )
        except IntegrityError:
            form.add_error(None, "No se pudo guardar. Revisa SKU y código de barras.")
        else:
            messages.success(request, "Artículo creado correctamente.")
            return redirect("bodega_web")
    return render(request, "catalogo/articulo_form.html", {"form": form})


@facultad_requerida("ingresar_articulo")
def catalogo_crear(request):
    form = ArticuloCatalogoForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                articulo = form.save()
                BitacoraAuditoria.objects.create(
                    usuario=request.user, accion="CREAR", entidad="Articulo",
                    id_registro=articulo.pk, valor_anterior=None,
                    valor_nuevo=datos_articulo(articulo),
                    detalle="Creación desde el catálogo.",
                )
            _guardar_especificaciones(articulo, form.cleaned_data.get("especificaciones"))
        except IntegrityError:
            form.add_error(None, "No se pudo guardar. Revisa SKU y código de barras.")
        else:
            messages.success(request, "Insumo creado correctamente.")
            return redirect("catalogo_listar")
    return render(request, "catalogo/catalogo.html", {
        "form": form, "titulo": "Crear insumo", "perm": permisos_de(request.user),
    })


@facultad_requerida("editar_articulo")
def catalogo_editar(request, pk):
    articulo = get_object_or_404(Articulo, pk=pk)
    anterior = datos_articulo(articulo)
    form = ArticuloCatalogoForm(
        request.POST if request.method == "POST" else None,
        instance=articulo,
    )
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                articulo = form.save()
                BitacoraAuditoria.objects.create(
                    usuario=request.user, accion="EDITAR", entidad="Articulo",
                    id_registro=articulo.pk, valor_anterior=anterior,
                    valor_nuevo=datos_articulo(articulo),
                    detalle="Actualización desde el catálogo.",
                )
            _guardar_especificaciones(articulo, form.cleaned_data.get("especificaciones"))
        except IntegrityError:
            form.add_error(None, "No se pudo guardar. Revisa SKU y código de barras.")
        else:
            messages.success(request, "Insumo actualizado correctamente.")
            return redirect("catalogo_listar")
    return render(request, "catalogo/catalogo.html", {
        "form": form, "titulo": "Editar insumo", "perm": permisos_de(request.user),
    })


@facultad_requerida("eliminar_articulo")
def catalogo_eliminar(request, pk):
    articulo = get_object_or_404(Articulo, pk=pk)
    if request.method == "POST":
        try:
            with transaction.atomic():
                articulo = get_object_or_404(
                    Articulo.objects.select_for_update(), pk=pk
                )
                if Despacho.objects.filter(articulo=articulo).exists():
                    messages.error(request, "No se puede eliminar: el insumo tiene despachos.")
                    return redirect("catalogo_listar")
                if articulo.stock_fisico != 0:
                    messages.error(request, "No se puede eliminar un insumo con stock físico.")
                    return redirect("catalogo_listar")
                with connection.cursor() as cursor:
                    tablas = connection.introspection.table_names(cursor=cursor)
                    if "bodega_detallefactura" in tablas:
                        cursor.execute(
                            "SELECT EXISTS (SELECT 1 FROM bodega_detallefactura WHERE articulo_id = %s)",
                            [articulo.pk],
                        )
                        if cursor.fetchone()[0]:
                            messages.error(request, "No se puede eliminar: el insumo tiene compras.")
                            return redirect("catalogo_listar")
                articulo_id = articulo.pk
                anterior = datos_articulo(articulo)
                articulo.delete()
                BitacoraAuditoria.objects.create(
                    usuario=request.user, accion="ELIMINAR", entidad="Articulo",
                    id_registro=articulo_id, valor_anterior=anterior,
                    valor_nuevo=None, detalle="Eliminación de insumo sin movimientos.",
                )
        except ProtectedError:
            messages.error(request, "No se puede eliminar: tiene registros asociados.")
        except IntegrityError as error:
            causa = error.__cause__
            codigo = getattr(causa, "pgcode", None) or getattr(causa, "sqlstate", None)
            if codigo != "23503":
                raise
            messages.error(request, "Existen relaciones que impiden eliminar el insumo.")
        else:
            messages.success(request, "Insumo eliminado correctamente.")
        return redirect("catalogo_listar")
    return render(request, "catalogo/catalogo.html", {
        "articulo": articulo, "perm": permisos_de(request.user),
    })


@login_required
@facultad_requerida("ver_catalogo")
def articulo_detalle(request, pk):
    articulo = get_object_or_404(Articulo, pk=pk)
    precios = DetalleFacturaCompra.objects.filter(
        articulo=articulo
    ).select_related("factura", "factura__proveedor").order_by(
        "-factura__fecha", "-id"
    )[:3]
    for detalle in precios:
        detalle.precio_neto_unitario = (
            detalle.subtotal_neto / detalle.cantidad
        ).quantize(Decimal("0.01"))
    return render(request, "catalogo/articulo_detalle.html", {
        "articulo": articulo,
        "precios": precios,
        "perm": permisos_de(request.user),
    })


@login_required
@facultad_requerida("ver_catalogo")
def articulo_exportar_precios_excel(request, pk):
    articulo = get_object_or_404(Articulo, pk=pk)
    precios = DetalleFacturaCompra.objects.filter(
        articulo=articulo
    ).select_related("factura", "factura__proveedor").order_by(
        "-factura__fecha", "-id"
    )[:3]

    libro = Workbook()
    hoja = libro.active
    hoja.title = "Precios"
    hoja.append([
        "Fecha",
        "Factura",
        "Proveedor",
        "Precio Unitario",
        "Descuentos",
        "Precio Neto Final",
    ])
    for detalle in precios:
        descuentos = "{}% / {}% / {}%".format(
            detalle.descuento_1,
            detalle.descuento_2,
            detalle.descuento_3,
        )
        precio_neto_unitario = (
            detalle.subtotal_neto / detalle.cantidad
        ).quantize(Decimal("0.01"))
        hoja.append([
            detalle.factura.fecha.strftime("%d-%m-%Y %H:%M"),
            detalle.factura.numero_factura,
            detalle.factura.proveedor.nombre,
            float(detalle.precio_unitario),
            descuentos,
            float(precio_neto_unitario),
        ])

    respuesta = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    respuesta["Content-Disposition"] = (
        f'attachment; filename="precios_{articulo.sku}.xlsx"'
    )
    libro.save(respuesta)
    return respuesta
