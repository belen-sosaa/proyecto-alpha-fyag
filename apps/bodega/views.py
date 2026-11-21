from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count, F, Q
from django.shortcuts import redirect, render
from django.contrib import messages

from apps.cuentas.roles import ACCESO, SUPERVISOR, permisos_de
from apps.cuentas.decorators import facultad_requerida
from .forms import CompraFacturaForm, DevolucionProveedorForm, MermaForm
from .models import (
    Articulo,
    BitacoraAuditoria,
    ConteoFisico,
    Despacho,
    DetalleConteoFisico,
    DetalleFacturaCompra,
    DevolucionProveedor,
    FacturaCompra,
    Merma,
)


def contexto_operativo(usuario):
    permisos = permisos_de(usuario)
    rol = permisos["rol"]
    modulos = ACCESO.get(rol, ())
    recepcion = rol == SUPERVISOR
    mostrar_bodega = permisos["despachar"] or permisos["registrar_merma"] or recepcion
    mostrar_catalogo = "catalogo" in modulos or "cardex" in modulos
    mostrar_finanzas = "compras" in modulos and permisos["ver_costos"]
    return {
        "panel_operativo": True,
        "rol_actual": rol,
        "perm": permisos,
        "recepcion": recepcion,
        "mostrar_bodega": mostrar_bodega,
        "mostrar_catalogo": mostrar_catalogo,
        "mostrar_finanzas": mostrar_finanzas,
    }


@login_required
def vista_inicio(request):
    contexto = contexto_operativo(request.user)
    contexto["titulo_panel"] = "Inicio"
    if contexto["mostrar_catalogo"]:
        contexto["resumen_stock"] = Articulo.objects.aggregate(
            total=Count("pk"),
            criticos=Count("pk", filter=Q(stock_fisico__lte=F("stock_critico"))),
        )
    if contexto["perm"]["despachar"]:
        contexto["despachos_recientes"] = Despacho.objects.select_related(
            "articulo", "obra"
        ).order_by("-fecha_despacho", "-pk")[:5]
    return render(request, "cuentas/inicio.html", contexto)


@login_required
def vista_bodega(request):
    contexto = contexto_operativo(request.user)
    if not contexto["mostrar_bodega"]:
        return redirect("inventario" if contexto["mostrar_catalogo"] else "inicio")
    contexto["titulo_panel"] = (
        "Recepción de despachos" if contexto["recepcion"] else "Bodega y Despachos"
    )
    if contexto["perm"]["despachar"]:
        contexto["articulos"] = Articulo.objects.order_by("sku")
    if contexto["perm"]["registrar_merma"]:
        contexto["merma_form"] = MermaForm()
        contexto["articulos_merma"] = Articulo.objects.order_by("sku")
    return render(request, "bodega/bodega.html", contexto)


@login_required
def vista_inventario(request):
    contexto = contexto_operativo(request.user)
    if not contexto["mostrar_catalogo"]:
        raise PermissionDenied
    contexto["titulo_panel"] = "Catálogo y Auditoría"
    contexto["articulos"] = Articulo.objects.select_related("categoria").order_by("sku")
    return render(request, "bodega/inventario.html", contexto)


@login_required
@facultad_requerida("ver_catalogo")
def vista_cardex(request):
    contexto = contexto_operativo(request.user)
    contexto["titulo_panel"] = "Cardex de movimientos"
    contexto["movimientos"] = BitacoraAuditoria.objects.select_related(
        "usuario"
    ).order_by("-fecha_hora", "-id")
    return render(request, "bodega/cardex.html", contexto)


@login_required
@facultad_requerida("ver_costos")
def vista_compras(request):
    contexto = contexto_operativo(request.user)
    if not contexto["mostrar_finanzas"]:
        raise PermissionDenied
    contexto["titulo_panel"] = "Compras y Finanzas"
    form = CompraFacturaForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                articulo = Articulo.objects.select_for_update().get(
                    pk=form.cleaned_data["articulo"].pk
                )
                cantidad = form.cleaned_data["cantidad"]
                precio_unitario = form.cleaned_data["precio_unitario"]
                descuento_1 = form.cleaned_data["descuento_1"]
                descuento_2 = form.cleaned_data["descuento_2"]
                descuento_3 = form.cleaned_data["descuento_3"]

                subtotal = precio_unitario * cantidad
                for descuento in (descuento_1, descuento_2, descuento_3):
                    subtotal = subtotal * (Decimal("100") - descuento) / Decimal("100")
                subtotal = subtotal.quantize(Decimal("0.01"))

                factura = FacturaCompra(
                    numero_factura=form.cleaned_data["numero_factura"],
                    proveedor=form.cleaned_data["proveedor"],
                    usuario=request.user,
                    total_neto=subtotal,
                )
                factura.save()
                DetalleFacturaCompra.objects.create(
                    factura=factura,
                    articulo=articulo,
                    cantidad=cantidad,
                    precio_unitario=precio_unitario,
                    descuento_1=descuento_1,
                    descuento_2=descuento_2,
                    descuento_3=descuento_3,
                    subtotal_neto=subtotal,
                )
                articulo.stock_fisico += cantidad
                articulo.save(update_fields=["stock_fisico"])
                BitacoraAuditoria.objects.create(
                    usuario=request.user,
                    accion="COMPRA",
                    entidad="FacturaCompra",
                    id_registro=factura.pk,
                    valor_anterior=None,
                    valor_nuevo={
                        "numero_factura": factura.numero_factura,
                        "articulo": articulo.pk,
                        "cantidad": cantidad,
                        "subtotal_neto": str(subtotal),
                    },
                    detalle="Ingreso de factura de compra.",
                )
        except Articulo.DoesNotExist:
            form.add_error("articulo", "El artículo seleccionado no existe.")
        else:
            messages.success(request, "Factura registrada correctamente.")
            return redirect("compras")
    contexto["form"] = form
    contexto["facturas"] = FacturaCompra.objects.select_related(
        "proveedor", "usuario"
    ).order_by("-fecha", "-pk")
    return render(request, "bodega/compras.html", contexto)


@login_required
@facultad_requerida("registrar_merma")
def merma_crear(request):
    form = MermaForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                articulo = Articulo.objects.select_for_update().get(
                    pk=form.cleaned_data["articulo"].pk
                )
                cantidad = form.cleaned_data["cantidad"]
                if cantidad <= 0:
                    form.add_error("cantidad", "La cantidad debe ser mayor que cero.")
                elif cantidad > articulo.stock_fisico:
                    form.add_error("cantidad", "La cantidad supera el stock disponible.")
                else:
                    anterior = articulo.stock_fisico
                    articulo.stock_fisico -= cantidad
                    articulo.save(update_fields=["stock_fisico"])
                    merma = Merma.objects.create(
                        articulo=articulo,
                        cantidad=cantidad,
                        motivo=form.cleaned_data["motivo"],
                        usuario=request.user,
                    )
                    BitacoraAuditoria.objects.create(
                        usuario=request.user,
                        accion="MERMA",
                        entidad="Merma",
                        id_registro=merma.pk,
                        valor_anterior={"stock_fisico": anterior},
                        valor_nuevo={"stock_fisico": articulo.stock_fisico},
                        detalle=merma.motivo,
                    )
        except Articulo.DoesNotExist:
            form.add_error("articulo", "El artículo seleccionado no existe.")
        else:
            if not form.errors:
                messages.success(request, "Merma registrada correctamente.")
                return redirect("bodega_web")
    contexto = contexto_operativo(request.user)
    contexto["titulo_panel"] = "Registro de Mermas"
    contexto["merma_form"] = form
    contexto["articulos_merma"] = Articulo.objects.order_by("sku")
    return render(request, "bodega/bodega.html", contexto)


@login_required
@facultad_requerida("registrar_merma")
def devolucion_crear(request):
    form = DevolucionProveedorForm(
        request.POST if request.method == "POST" else None
    )
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                articulo = Articulo.objects.select_for_update().get(
                    pk=form.cleaned_data["articulo"].pk
                )
                cantidad = form.cleaned_data["cantidad"]
                if cantidad <= 0:
                    form.add_error("cantidad", "La cantidad debe ser mayor que cero.")
                elif cantidad > articulo.stock_fisico:
                    form.add_error("cantidad", "La cantidad supera el stock disponible.")
                else:
                    anterior = articulo.stock_fisico
                    devolucion = DevolucionProveedor.objects.create(
                        proveedor=form.cleaned_data["proveedor"],
                        articulo=articulo,
                        cantidad=cantidad,
                        motivo=form.cleaned_data["motivo"],
                        usuario=request.user,
                    )
                    articulo.stock_fisico -= cantidad
                    articulo.save(update_fields=["stock_fisico"])
                    BitacoraAuditoria.objects.create(
                        usuario=request.user,
                        accion="DEVOLUCION_PROVEEDOR",
                        entidad="DevolucionProveedor",
                        id_registro=devolucion.pk,
                        valor_anterior={"stock_fisico": anterior},
                        valor_nuevo={"stock_fisico": articulo.stock_fisico},
                        detalle=devolucion.motivo,
                    )
        except Articulo.DoesNotExist:
            form.add_error("articulo", "El artículo seleccionado no existe.")
        else:
            if not form.errors:
                messages.success(request, "Devolución registrada correctamente.")
                return redirect("devolucion_crear")
    contexto = contexto_operativo(request.user)
    contexto["titulo_panel"] = "Devoluciones a proveedores"
    contexto["form"] = form
    contexto["devoluciones"] = DevolucionProveedor.objects.select_related(
        "proveedor", "articulo", "usuario"
    ).order_by("-fecha", "-pk")
    return render(request, "bodega/devoluciones.html", contexto)


@login_required
@facultad_requerida("ingresar_articulo")
def conteo_crear(request):
    articulos = Articulo.objects.select_related("categoria").order_by("sku")
    if request.method == "POST":
        observacion = request.POST.get("observacion", "").strip()
        errores = []
        cantidades = {}
        for articulo in articulos:
            valor = request.POST.get(f"stock_contado_{articulo.pk}", "").strip()
            try:
                stock_contado = int(valor)
            except (TypeError, ValueError):
                errores.append(
                    f"El conteo de {articulo.descripcion} debe ser un número entero."
                )
                continue
            if stock_contado < 0:
                errores.append(
                    f"El conteo de {articulo.descripcion} no puede ser negativo."
                )
                continue
            cantidades[articulo.pk] = stock_contado
        if errores:
            contexto = contexto_operativo(request.user)
            contexto["titulo_panel"] = "Conteo físico"
            contexto["articulos"] = articulos
            contexto["errores_conteo"] = errores
            contexto["cantidades"] = cantidades
            return render(request, "bodega/conteo_fisico.html", contexto)
        with transaction.atomic():
            conteo = ConteoFisico.objects.create(
                usuario=request.user,
                observacion=observacion,
            )
            for articulo in articulos.select_for_update():
                DetalleConteoFisico.objects.create(
                    conteo=conteo,
                    articulo=articulo,
                    stock_sistema=articulo.stock_fisico,
                    stock_contado=cantidades[articulo.pk],
                )
        messages.success(request, "Conteo físico registrado correctamente.")
        return redirect("conteo_discrepancias")
    contexto = contexto_operativo(request.user)
    contexto["titulo_panel"] = "Conteo físico"
    contexto["articulos"] = articulos
    return render(request, "bodega/conteo_fisico.html", contexto)


@login_required
@facultad_requerida("ver_catalogo")
def conteo_discrepancias(request):
    conteo_id = request.GET.get("conteo")
    if conteo_id:
        conteo = ConteoFisico.objects.filter(pk=conteo_id).first()
    else:
        conteo = ConteoFisico.objects.order_by("-fecha", "-pk").first()
    diferencias = []
    if conteo:
        diferencias = DetalleConteoFisico.objects.filter(
            conteo=conteo,
        ).exclude(stock_contado=F("stock_sistema")).select_related("articulo")
    contexto = contexto_operativo(request.user)
    contexto["titulo_panel"] = "Discrepancias de inventario"
    contexto["conteo"] = conteo
    contexto["conteos"] = ConteoFisico.objects.order_by("-fecha", "-pk")
    contexto["diferencias"] = diferencias
    return render(request, "bodega/discrepancias.html", contexto)
