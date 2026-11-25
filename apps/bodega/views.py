from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import connection, transaction
from django.db.models import Count, F, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render

from apps.cuentas.decorators import facultad_requerida
from apps.cuentas.roles import ACCESO, SUPERVISOR, permisos_de
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
    # Inicio y los módulos comparten estos datos para mostrar el menú y el perfil según el rol.
    permisos = permisos_de(usuario)
    rol = permisos["rol"]
    modulos = ACCESO.get(rol, ())
    recepcion = rol == SUPERVISOR
    # Cada enlace refleja una función distinta. El Supervisor entra a Despachos para recibir.
    mostrar_bodega = permisos["registrar_merma"] or permisos["ingresar_articulo"]
    mostrar_despachos = permisos["despachar"] or recepcion
    mostrar_catalogo = permisos["ver_catalogo"]
    mostrar_auditoria = "cardex" in modulos
    mostrar_finanzas = "compras" in modulos and permisos["ver_costos"]
    return {
        "panel_operativo": True,
        "rol_actual": rol,
        "perm": permisos,
        "recepcion": recepcion,
        "mostrar_bodega": mostrar_bodega,
        "mostrar_despachos": mostrar_despachos,
        "mostrar_catalogo": mostrar_catalogo,
        "mostrar_auditoria": mostrar_auditoria,
        "mostrar_finanzas": mostrar_finanzas,
    }


@login_required
def vista_inicio(request):
    contexto = contexto_operativo(request.user)
    contexto["titulo_panel"] = "Inicio"
    if contexto["mostrar_catalogo"]:
        # El conteo compara cada existencia con su propio umbral directamente en la base de datos.
        contexto["resumen_stock"] = Articulo.objects.aggregate(
            total=Count("pk"),
            criticos=Count("pk", filter=Q(stock_fisico__lte=F("stock_critico"))),
        )
    if contexto["perm"]["despachar"]:
        # El ID desempata despachos con la misma fecha y mantiene un orden estable.
        contexto["despachos_recientes"] = Despacho.objects.select_related(
            "articulo", "obra"
        ).order_by("-fecha_despacho", "-pk")[:5]
    return render(request, "cuentas/inicio.html", contexto)


@login_required
def vista_bodega(request):
    contexto = contexto_operativo(request.user)
    if not contexto["mostrar_bodega"]:
        if contexto["mostrar_despachos"]:
            return redirect("despachos_web")
        return redirect("inventario" if contexto["mostrar_catalogo"] else "inicio")
    contexto["titulo_panel"] = "Bodega"
    if contexto["perm"]["registrar_merma"]:
        contexto["merma_form"] = MermaForm()
    return render(request, "bodega/bodega.html", contexto)


@login_required
def vista_inventario(request):
    contexto = contexto_operativo(request.user)
    if not contexto["mostrar_catalogo"]:
        raise PermissionDenied
    contexto["titulo_panel"] = "Catálogo"
    # Se trae la categoría junto con cada artículo para evitar consultarla fila por fila.
    contexto["articulos"] = Articulo.objects.select_related("categoria").order_by("sku")
    return render(request, "bodega/inventario.html", contexto)


@login_required
def vista_auditoria(request):
    contexto = contexto_operativo(request.user)
    if not contexto["mostrar_auditoria"]:
        raise PermissionDenied
    contexto["titulo_panel"] = "Auditor?a"
    contexto["movimientos"] = BitacoraAuditoria.objects.select_related(
        "usuario"
    ).order_by("-fecha_hora", "-id")
    return render(request, "bodega/cardex.html", contexto)


@login_required
def vista_compras(request):
    contexto = contexto_operativo(request.user)
    if not contexto["mostrar_finanzas"]:
        raise PermissionDenied
    contexto["titulo_panel"] = "Compras"
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
    articulos = Articulo.objects.order_by("sku")
    contexto = contexto_operativo(request.user)
    contexto["titulo_panel"] = "Conteo físico"
    if request.method == "POST":
        observacion = request.POST.get("observacion", "").strip()
        contexto["observacion"] = observacion
        errores = []
        filas = []
        campo_stock = DetalleConteoFisico._meta.get_field("stock_contado")
        _, maximo = connection.ops.integer_field_range(campo_stock.get_internal_type())
        with transaction.atomic():
            detalles = []
            for articulo in articulos.select_for_update():
                valor = request.POST.get(f"stock_contado_{articulo.pk}", "").strip()
                filas.append((articulo, valor))
                try:
                    stock_contado = int(valor)
                except ValueError:
                    errores.append(f"El conteo de {articulo.descripcion} debe ser un número entero.")
                    continue
                if stock_contado < 0:
                    errores.append(f"El conteo de {articulo.descripcion} no puede ser negativo.")
                    continue
                if maximo is not None and stock_contado > maximo:
                    errores.append(f"El conteo de {articulo.descripcion} supera el valor máximo permitido.")
                    continue
                detalles.append(DetalleConteoFisico(
                    articulo=articulo,
                    stock_sistema=articulo.stock_fisico,
                    stock_contado=stock_contado,
                ))
            if not errores:
                conteo = ConteoFisico.objects.create(
                    usuario=request.user,
                    observacion=observacion,
                )
                for detalle in detalles:
                    detalle.conteo = conteo
                DetalleConteoFisico.objects.bulk_create(detalles)
        if not errores:
            messages.success(request, "Conteo físico registrado correctamente.")
            return redirect("conteo_discrepancias")
        contexto["errores_conteo"] = errores
    else:
        filas = [(articulo, articulo.stock_fisico) for articulo in articulos]
    contexto["filas"] = filas
    return render(request, "bodega/conteo_fisico.html", contexto)


@login_required
@facultad_requerida("ver_catalogo")
def conteo_discrepancias(request):
    conteo_id = request.GET.get("conteo")
    conteos = ConteoFisico.objects.order_by("-fecha", "-pk")
    if conteo_id:
        try:
            conteo_id = int(conteo_id)
        except ValueError as error:
            raise Http404("El número de conteo no es válido.") from error
        conteo = get_object_or_404(conteos, pk=conteo_id)
    else:
        conteo = conteos.first()
    diferencias = []
    if conteo:
        diferencias = DetalleConteoFisico.objects.filter(
            conteo=conteo,
        ).exclude(stock_contado=F("stock_sistema")).select_related("articulo")
    contexto = contexto_operativo(request.user)
    contexto["titulo_panel"] = "Discrepancias de inventario"
    contexto["conteo"] = conteo
    contexto["conteos"] = conteos
    contexto["diferencias"] = diferencias
    return render(request, "bodega/discrepancias.html", contexto)
