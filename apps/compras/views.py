import json
import logging
from decimal import ROUND_HALF_UP, Decimal
from functools import wraps

from django import forms
from django.db import DatabaseError, IntegrityError, transaction
from django.db.models import F, OuterRef, Q, Subquery
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST

from apps.bodega.models import Articulo, BitacoraAuditoria, MovimientoCardex
from apps.bodega.respaldo import documento_cardex, guardar_respaldo_cardex
from apps.cuentas.decorators import facultad_requerida
from apps.cuentas.roles import puede
from .models import DetalleFactura, EntidadComercial, FacturaCompra


logger = logging.getLogger(__name__)
MAX_STOCK = 2_147_483_647
MAX_IMPORTE = Decimal("9999999999.99")
CENTAVO = Decimal("0.01")
TASA_IVA = Decimal("0.19")


class EncabezadoFacturaForm(forms.Form):
    proveedor_id = forms.IntegerField(min_value=1, max_value=2**63 - 1)
    folio = forms.CharField(max_length=50)
    fecha_emision = forms.DateField(input_formats=["%Y-%m-%d"])


class DetalleFacturaForm(forms.Form):
    articulo_id = forms.IntegerField(min_value=1, max_value=2**63 - 1)
    cantidad = forms.IntegerField(min_value=1, max_value=MAX_STOCK)
    precio_unitario_neto = forms.DecimalField(min_value=0, max_digits=12, decimal_places=2)
    descuento_1 = forms.DecimalField(required=False, min_value=0, max_value=100, decimal_places=2)
    descuento_2 = forms.DecimalField(required=False, min_value=0, max_value=100, decimal_places=2)
    descuento_3 = forms.DecimalField(required=False, min_value=0, max_value=100, decimal_places=2)


class ConfirmarIngresoForm(forms.Form):
    factura_id = forms.IntegerField(min_value=1, max_value=2**63 - 1)


class ConsultaPreciosForm(forms.Form):
    articulo = forms.ModelChoiceField(
        queryset=Articulo.objects.only("sku", "descripcion").order_by("sku"),
        label="Artículo", empty_label="Selecciona un artículo",
    )


class StockFueraDeRango(Exception):
    pass


def error_json(codigo, mensaje, estado=400, campos=None):
    return JsonResponse({"codigo": codigo, "mensaje": mensaje, "campos": campos or {}}, status=estado)


def permiso_compra(*facultades):
    def decorador(vista):
        @wraps(vista)
        def protegida(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return error_json("SESION_REQUERIDA", "Inicia sesión para continuar.", 401)
            if not any(puede(request.user, facultad) for facultad in facultades):
                return error_json("SIN_PERMISO", "Tu rol no permite esta operación.", 403)
            return vista(request, *args, **kwargs)
        return protegida
    return decorador


def cuerpo_json(request):
    try:
        datos = json.loads(request.body, parse_float=Decimal)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return datos if isinstance(datos, dict) else None


@require_GET
@permiso_compra("registrar_factura", "confirmar_ingreso")
def listar_proveedores(request):
    proveedores = EntidadComercial.proveedores()
    consulta = request.GET.get("q", "").strip()
    if consulta:
        proveedores = proveedores.filter(Q(rut__icontains=consulta) | Q(razon_social__icontains=consulta))
    datos = proveedores.order_by("razon_social", "id").values("id", "rut", "razon_social")
    return JsonResponse(list(datos), safe=False)


def _ultimas_compras(articulo_id):
    detalles = DetalleFactura.objects.filter(articulo_id=articulo_id)
    # Si una factura repite el artículo, su última línea representa esa compra.
    ultimo_detalle = detalles.filter(factura_id=OuterRef("factura_id")).order_by("-pk").values("pk")[:1]
    return detalles.filter(pk=Subquery(ultimo_detalle), factura__tipo_documento__iexact="FACTURA").annotate(
        folio=F("factura__folio"), proveedor_id=F("factura__proveedor_id"),
        fecha_emision=F("factura__fecha_emision"),
    ).order_by("-fecha_emision", "-factura_id", "-pk")[:3]


@require_GET
@permiso_compra("ver_costos")
def consultar_ultimos_precios(request, articulo_id):
    if not Articulo.objects.filter(pk=articulo_id).exists():
        return error_json("ARTICULO_NO_ENCONTRADO", "El artículo no existe.", 404)
    precios = _ultimas_compras(articulo_id).values(
        "factura_id", "folio", "proveedor_id", "fecha_emision", "costo_unitario_neto_final",
    )
    return JsonResponse(list(precios), safe=False)


@require_GET
@facultad_requerida("ver_costos")
def vista_ultimos_precios(request):
    formulario = ConsultaPreciosForm(request.GET if "articulo" in request.GET else None)
    contexto = {"formulario": formulario}
    if formulario.is_bound and formulario.is_valid():
        articulo = formulario.cleaned_data["articulo"]
        contexto["articulo_seleccionado"] = articulo
        contexto["precios"] = _ultimas_compras(articulo.pk).values(
            "folio", "fecha_emision", "factura__proveedor__razon_social", "costo_unitario_neto_final",
        )
    return render(request, "compras/ultimos_precios.html", contexto)


@require_POST
@permiso_compra("registrar_factura")
def registrar_factura(request):
    datos = cuerpo_json(request)
    if datos is None:
        return error_json("DATOS_INVALIDOS", "Envía los datos de la factura como un objeto JSON.")

    encabezado = EncabezadoFacturaForm({
        **datos, "fecha_emision": datos.get("fecha_emision") if isinstance(datos.get("fecha_emision"), str) else None,
    })
    if not encabezado.is_valid():
        return error_json("DATOS_INVALIDOS", "Revisa los datos de la factura.", campos=dict(encabezado.errors))
    detalles = datos.get("detalles")
    if not isinstance(detalles, list) or not detalles or not all(isinstance(linea, dict) for linea in detalles):
        return error_json("DATOS_INVALIDOS", "La factura debe tener al menos una línea de detalle.")

    lineas = []
    subtotal = Decimal("0.00")
    for indice, detalle in enumerate(detalles):
        formulario = DetalleFacturaForm(detalle)
        if not formulario.is_valid():
            campos = {f"detalles.{indice}.{campo}": errores for campo, errores in formulario.errors.items()}
            return error_json("DATOS_INVALIDOS", "Revisa los datos del detalle.", campos=campos)
        linea = formulario.cleaned_data
        costo = linea["precio_unitario_neto"]
        for campo in ("descuento_1", "descuento_2", "descuento_3"):
            descuento = linea[campo] or Decimal("0.00")
            linea[campo] = descuento
            costo *= 1 - descuento / 100
        linea["costo_unitario_neto_final"] = costo.quantize(CENTAVO, rounding=ROUND_HALF_UP)
        subtotal += linea["cantidad"] * linea["costo_unitario_neto_final"]
        lineas.append(linea)

    iva = (subtotal * TASA_IVA).quantize(CENTAVO, rounding=ROUND_HALF_UP)
    total = subtotal + iva
    if total > MAX_IMPORTE:
        return error_json("DATOS_INVALIDOS", "El total supera el importe admitido para la factura.")

    datos_factura = encabezado.cleaned_data
    if not EntidadComercial.proveedores().filter(pk=datos_factura["proveedor_id"]).exists():
        return error_json("PROVEEDOR_NO_ENCONTRADO", "Selecciona un proveedor válido.", 404)
    ids = {linea["articulo_id"] for linea in lineas}
    if Articulo.objects.filter(pk__in=ids).count() != len(ids):
        return error_json("ARTICULO_NO_ENCONTRADO", "Uno o más artículos no existen.", 404)

    try:
        with transaction.atomic():
            factura = FacturaCompra.objects.create(
                **datos_factura, usuario_registro=request.user, subtotal_neto=subtotal, iva=iva, total=total,
            )
            detalles_guardados = DetalleFactura.objects.bulk_create([
                DetalleFactura(factura=factura, **linea) for linea in lineas
            ])
    except IntegrityError:
        if FacturaCompra.objects.filter(
            proveedor_id=datos_factura["proveedor_id"], folio=datos_factura["folio"], tipo_documento="FACTURA",
        ).exists():
            return error_json("FACTURA_DUPLICADA", "El proveedor ya tiene una factura con ese folio.", 409)
        logger.exception("Falló el guardado de una factura de compra.")
        return error_json("ERROR_FACTURA", "No se pudo guardar la factura.", 500)

    return JsonResponse({
        "id": factura.pk, **datos_factura, "estado_ingreso": factura.estado_ingreso,
        "subtotal_neto": subtotal, "iva": iva, "total": total,
        "detalles": [{"id": detalle.pk, **linea} for detalle, linea in zip(detalles_guardados, lineas)],
    }, status=201)


@require_POST
@permiso_compra("confirmar_ingreso")
def confirmar_ingreso(request):
    datos = cuerpo_json(request)
    if datos is None:
        return error_json("DATOS_INVALIDOS", "Envía el identificador de la factura como un objeto JSON.")
    formulario = ConfirmarIngresoForm(datos)
    if not formulario.is_valid():
        return error_json("DATOS_INVALIDOS", "Selecciona una factura válida.", campos=dict(formulario.errors))

    try:
        with transaction.atomic():
            factura = FacturaCompra.objects.select_for_update().get(pk=formulario.cleaned_data["factura_id"])
            if factura.estado_ingreso == FacturaCompra.EstadoIngreso.CONFIRMADO:
                return error_json("INGRESO_YA_CONFIRMADO", "La factura ya tiene un ingreso confirmado.", 409)
            if factura.estado_ingreso != FacturaCompra.EstadoIngreso.PENDIENTE:
                return error_json("FACTURA_HISTORICA", "La factura histórica requiere revisión antes de afectar el stock.", 409)
            detalles = list(factura.detalles.order_by("pk"))
            if not detalles or any(detalle.cantidad <= 0 for detalle in detalles):
                return error_json("FACTURA_SIN_DETALLE_VALIDO", "La factura necesita líneas con cantidades positivas.", 409)

            # El orden fijo evita bloqueos cruzados entre facturas que comparten artículos.
            articulos = {articulo.pk: articulo for articulo in Articulo.objects.select_for_update().filter(
                pk__in={detalle.articulo_id for detalle in detalles},
            ).order_by("pk")}
            movimientos = []
            documentos = []
            for detalle in detalles:
                articulo = articulos[detalle.articulo_id]
                stock_anterior = articulo.stock_fisico
                articulo.stock_fisico += detalle.cantidad
                if articulo.stock_fisico > MAX_STOCK:
                    raise StockFueraDeRango
                articulo.save(update_fields=["stock_fisico"])
                movimiento = MovimientoCardex.objects.create(
                    tipo=MovimientoCardex.Tipo.INGRESO, articulo=articulo, usuario=request.user,
                    cantidad=detalle.cantidad, stock_resultante=articulo.stock_fisico,
                )
                BitacoraAuditoria.objects.create(
                    usuario=request.user, accion="CREAR", entidad="MovimientoCardex", id_registro=movimiento.pk,
                    valor_anterior={"stock_fisico": stock_anterior}, valor_nuevo={
                        "stock_fisico": articulo.stock_fisico, "factura_id": factura.pk,
                        "detalle_factura_id": detalle.pk, "proveedor_id": factura.proveedor_id,
                        "articulo_id": articulo.pk, "cantidad": detalle.cantidad,
                    }, detalle=f"Ingreso de factura {factura.folio} confirmado.",
                )
                movimientos.append({
                    "id": movimiento.pk, "tipo": movimiento.tipo, "articulo_id": articulo.pk,
                    "usuario_id": request.user.pk, "cantidad": movimiento.cantidad,
                    "stock_resultante": movimiento.stock_resultante, "obra_id": None,
                    "fecha_hora": movimiento.fecha_hora.isoformat(), "justificacion": None,
                    "factura_id": factura.pk, "detalle_factura_id": detalle.pk, "proveedor_id": factura.proveedor_id,
                })
                documentos.append(documento_cardex(
                    movimiento, operacion="ingreso_factura", factura_id=factura.pk,
                    detalle_factura_id=detalle.pk, proveedor_id=factura.proveedor_id,
                ))
            factura.estado_ingreso = FacturaCompra.EstadoIngreso.CONFIRMADO
            factura.save(update_fields=["estado_ingreso"])
            transaction.on_commit(lambda: guardar_respaldo_cardex(documentos), robust=True)
    except FacturaCompra.DoesNotExist:
        return error_json("FACTURA_NO_ENCONTRADA", "La factura no existe.", 404)
    except StockFueraDeRango:
        return error_json("STOCK_FUERA_DE_RANGO", "El ingreso supera el stock máximo admitido.", 409)
    except DatabaseError:
        logger.exception("Falló la transacción de ingreso de materiales.")
        return error_json("ERROR_INGRESO", "No se pudo confirmar el ingreso. Vuelve a intentarlo.", 500)

    return JsonResponse({"factura_id": factura.pk, "movimientos": movimientos}, status=201)
