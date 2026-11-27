import json
import logging
from collections import Counter
from decimal import ROUND_HALF_UP, Decimal
from functools import wraps

from django import forms
from django.contrib import messages
from django.db import DatabaseError, IntegrityError, transaction
from django.db.models import F, OuterRef, Q, Subquery, Sum
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
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
MAX_LINEAS = 50
TIPO = FacturaCompra.TipoDocumento
ESTADO = FacturaCompra.EstadoIngreso


class EncabezadoFacturaForm(forms.Form):
    proveedor_id = forms.IntegerField(min_value=1, max_value=2**63 - 1)
    tipo_documento = forms.ChoiceField(choices=TIPO.choices, required=False, initial=TIPO.FACTURA)
    folio = forms.CharField(max_length=50)
    fecha_emision = forms.DateField(input_formats=["%Y-%m-%d"])
    documento_origen_id = forms.IntegerField(required=False, min_value=1, max_value=2**63 - 1)
    descuentos_generales = forms.DecimalField(required=False, min_value=0, max_digits=12, decimal_places=2)

    def clean_tipo_documento(self):
        # Los formularios antiguos solo registraban facturas y no enviaban este campo.
        return self.cleaned_data["tipo_documento"] or TIPO.FACTURA

    def clean_descuentos_generales(self):
        return self.cleaned_data["descuentos_generales"] or Decimal("0.00")


class DetalleFacturaForm(forms.Form):
    articulo_id = forms.IntegerField(min_value=1, max_value=2**63 - 1)
    cantidad = forms.IntegerField(min_value=1, max_value=MAX_STOCK)
    precio_unitario_neto = forms.DecimalField(required=False, min_value=0, max_digits=12, decimal_places=2)
    descuento_1 = forms.DecimalField(required=False, min_value=0, max_value=100, decimal_places=2)
    descuento_2 = forms.DecimalField(required=False, min_value=0, max_value=100, decimal_places=2)
    descuento_3 = forms.DecimalField(required=False, min_value=0, max_value=100, decimal_places=2)
    precio_lista = forms.DecimalField(required=False, min_value=0, max_digits=12, decimal_places=2)


class ConfirmarIngresoForm(forms.Form):
    factura_id = forms.IntegerField(min_value=1, max_value=2**63 - 1)


class ConsultaPreciosForm(forms.Form):
    articulo = forms.ModelChoiceField(
        queryset=Articulo.objects.only("sku", "descripcion").order_by("sku"),
        label="Artículo", empty_label="Selecciona un artículo",
    )


class StockFueraDeRango(Exception):
    pass


class DocumentoInvalido(Exception):
    pass


def _sumar_lineas(lineas):
    # Un artículo puede aparecer más de una vez. Para comparar documentos sumamos por artículo.
    cantidades, netos = Counter(), Counter()
    for articulo_id, cantidad, costo in lineas:
        cantidades[articulo_id] += cantidad
        netos[articulo_id] += cantidad * costo
    return cantidades, netos


def _validar_referencia(tipo, proveedor_id, origen, lineas, iva):
    # La guía registra lo recibido. Una factura puede respaldar esa guía y una nota corrige una factura.
    if tipo == TIPO.GUIA:
        if origen:
            raise DocumentoInvalido("Una guía de despacho no admite un documento de origen.")
        return
    if tipo == TIPO.FACTURA and not origen:
        return

    tipo_origen = TIPO.FACTURA if tipo == TIPO.NOTA_CREDITO else TIPO.GUIA
    if not origen or origen.tipo_documento != tipo_origen or origen.estado_ingreso != ESTADO.CONFIRMADO:
        if tipo == TIPO.NOTA_CREDITO:
            raise DocumentoInvalido("Selecciona la factura confirmada que se corrige con esta nota de crédito.")
        raise DocumentoInvalido("Selecciona la guía confirmada que respalda esta factura.")
    if origen.proveedor_id != proveedor_id:
        raise DocumentoInvalido("El documento de origen debe pertenecer al mismo proveedor.")

    campos = ("articulo_id", "cantidad", "costo_unitario_neto_final")
    cantidades_origen, netos_origen = _sumar_lineas(origen.detalles.values_list(*campos))
    cantidades, netos = _sumar_lineas(lineas)
    if tipo == TIPO.FACTURA:
        if cantidades != cantidades_origen:
            raise DocumentoInvalido("La factura debe coincidir con los artículos y cantidades de la guía.")
        return

    notas_confirmadas = origen.documentos_asociados.filter(
        tipo_documento=TIPO.NOTA_CREDITO, estado_ingreso=ESTADO.CONFIRMADO,
    )
    acreditadas, netos_acreditados = _sumar_lineas(
        DetalleFactura.objects.filter(factura__in=notas_confirmadas).values_list(*campos)
    )
    # Las notas ya aplicadas consumen parte del saldo de la factura. Las pendientes se revisan al confirmar.
    for articulo_id, cantidad in cantidades.items():
        if cantidad + acreditadas[articulo_id] > cantidades_origen[articulo_id]:
            raise DocumentoInvalido("La nota supera la cantidad de artículos aún disponible para acreditar en la factura.")
        if netos[articulo_id] + netos_acreditados[articulo_id] > netos_origen[articulo_id]:
            raise DocumentoInvalido("La nota supera el importe neto aún disponible para acreditar en la factura.")
    if iva + notas_confirmadas.aggregate(iva=Sum("iva", default=0))["iva"] > origen.iva:
        raise DocumentoInvalido("El IVA de la nota supera el saldo de IVA disponible en la factura.")


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


def ultimas_compras(articulo_id):
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
    precios = ultimas_compras(articulo_id).values(
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
        contexto["precios"] = ultimas_compras(articulo.pk).values(
            "folio", "fecha_emision", "factura__proveedor__razon_social", "costo_unitario_neto_final",
        )
    return render(request, "compras/ultimos_precios.html", contexto)


@require_POST
@permiso_compra("registrar_factura")
def registrar_factura(request):
    datos = cuerpo_json(request)
    if datos is None:
        return error_json("DATOS_INVALIDOS", "Envía los datos de la factura como un objeto JSON.")

    return _registrar_factura_datos(request.user, datos)


def _registrar_factura_datos(usuario, datos):
    # La pantalla y la API usan estas mismas reglas. Guardar el documento todavía no mueve el stock.
    encabezado = EncabezadoFacturaForm({
        **datos, "fecha_emision": datos.get("fecha_emision") if isinstance(datos.get("fecha_emision"), str) else None,
    })
    if not encabezado.is_valid():
        return error_json("DATOS_INVALIDOS", "Revisa los datos de la factura.", campos=dict(encabezado.errors))
    detalles = datos.get("detalles")
    if (not isinstance(detalles, list) or not 1 <= len(detalles) <= MAX_LINEAS
            or not all(isinstance(linea, dict) for linea in detalles)):
        return error_json("DATOS_INVALIDOS", f"El documento debe tener entre 1 y {MAX_LINEAS} líneas de detalle.")

    datos_factura = encabezado.cleaned_data
    tipo = datos_factura["tipo_documento"]
    lineas = []
    subtotal = Decimal("0.00")
    precios_lista = {}
    for indice, detalle in enumerate(detalles):
        formulario = DetalleFacturaForm(detalle)
        if not formulario.is_valid():
            campos = {f"detalles.{indice}.{campo}": errores for campo, errores in formulario.errors.items()}
            return error_json("DATOS_INVALIDOS", "Revisa los datos del detalle.", campos=campos)
        linea = formulario.cleaned_data
        costo = linea["precio_unitario_neto"]
        if costo is None and tipo != TIPO.GUIA:
            campos = {f"detalles.{indice}.precio_unitario_neto": ["Ingresa el precio unitario neto."]}
            return error_json("DATOS_INVALIDOS", "Revisa los datos del detalle.", campos=campos)
        if costo is None:
            costo = linea["precio_unitario_neto"] = Decimal("0.00")
        precio_lista = linea["precio_lista"]
        if precio_lista is not None:
            if tipo != TIPO.FACTURA:
                return error_json("DATOS_INVALIDOS", "El precio de lista se actualiza con una factura.")
            anterior = precios_lista.setdefault(linea["articulo_id"], precio_lista)
            if anterior != precio_lista:
                return error_json("DATOS_INVALIDOS", "Usa un solo precio de lista por artículo en el documento.")
        # Cada descuento se aplica sobre el precio que dejó el descuento anterior.
        for campo in ("descuento_1", "descuento_2", "descuento_3"):
            descuento = linea[campo] or Decimal("0.00")
            linea[campo] = descuento
            costo *= 1 - descuento / 100
        linea["costo_unitario_neto_final"] = costo.quantize(CENTAVO, rounding=ROUND_HALF_UP)
        subtotal += linea["cantidad"] * linea["costo_unitario_neto_final"]
        lineas.append(linea)

    descuentos_generales = datos_factura["descuentos_generales"]
    subtotal = max(Decimal("0.00"), subtotal - descuentos_generales)
    # En este flujo, la guía respalda la recepción. El IVA se calcula en la factura o la nota de crédito.
    iva = Decimal("0.00") if tipo == TIPO.GUIA else (subtotal * TASA_IVA).quantize(CENTAVO, rounding=ROUND_HALF_UP)
    total = subtotal + iva
    if total > MAX_IMPORTE:
        return error_json("DATOS_INVALIDOS", "El total supera el importe admitido para el documento.")

    if not EntidadComercial.proveedores().filter(pk=datos_factura["proveedor_id"]).exists():
        return error_json("PROVEEDOR_NO_ENCONTRADO", "Selecciona un proveedor válido.", 404)
    ids = {linea["articulo_id"] for linea in lineas}
    if Articulo.objects.filter(pk__in=ids).count() != len(ids):
        return error_json("ARTICULO_NO_ENCONTRADO", "Uno o más artículos no existen.", 404)

    try:
        with transaction.atomic():
            origen_id = datos_factura["documento_origen_id"]
            origen = FacturaCompra.objects.filter(pk=origen_id).first() if origen_id else None
            if origen_id and origen is None:
                raise DocumentoInvalido("El documento de origen no existe.")
            lineas_referencia = (
                (linea["articulo_id"], linea["cantidad"], linea["costo_unitario_neto_final"]) for linea in lineas
            )
            _validar_referencia(tipo, datos_factura["proveedor_id"], origen, lineas_referencia, iva)
            factura = FacturaCompra.objects.create(
                **datos_factura, usuario_registro=usuario, subtotal_neto=subtotal, iva=iva, total=total,
            )
            detalles_guardados = DetalleFactura.objects.bulk_create([
                DetalleFactura(factura=factura, **linea) for linea in lineas
            ])
    except DocumentoInvalido as error:
        return error_json("DOCUMENTO_INVALIDO", str(error))
    except IntegrityError:
        if FacturaCompra.objects.filter(
            proveedor_id=datos_factura["proveedor_id"], folio=datos_factura["folio"], tipo_documento=tipo,
        ).exists():
            return error_json("FACTURA_DUPLICADA", "El proveedor ya tiene un documento de ese tipo con ese folio.", 409)
        if tipo == TIPO.FACTURA and origen_id and FacturaCompra.objects.filter(
            documento_origen_id=origen_id, tipo_documento=TIPO.FACTURA,
        ).exists():
            return error_json("GUIA_YA_FACTURADA", "La guía ya está asociada a otra factura.", 409)
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
    return _confirmar_ingreso_datos(request.user, datos)


def _confirmar_ingreso_datos(usuario, datos):
    formulario = ConfirmarIngresoForm(datos)
    if not formulario.is_valid():
        return error_json("DATOS_INVALIDOS", "Selecciona una factura válida.", campos=dict(formulario.errors))

    try:
        # El documento, los cambios de inventario y la bitácora se guardan juntos.
        with transaction.atomic():
            # Bloqueamos el documento para que un segundo clic no aplique la misma operación dos veces.
            factura = FacturaCompra.objects.select_for_update().get(pk=formulario.cleaned_data["factura_id"])
            if factura.estado_ingreso == FacturaCompra.EstadoIngreso.CONFIRMADO:
                return error_json("INGRESO_YA_CONFIRMADO", "La factura ya tiene un ingreso confirmado.", 409)
            if factura.estado_ingreso != FacturaCompra.EstadoIngreso.PENDIENTE:
                return error_json("FACTURA_HISTORICA", "La factura histórica requiere revisión antes de afectar el stock.", 409)
            detalles = list(factura.detalles.order_by("pk"))
            if not detalles or any(detalle.cantidad <= 0 for detalle in detalles):
                return error_json("FACTURA_SIN_DETALLE_VALIDO", "La factura necesita líneas con cantidades positivas.", 409)

            # Si dos notas usan la misma factura, una espera a la otra antes de revisar cuánto queda por acreditar.
            origen = (
                FacturaCompra.objects.select_for_update().get(pk=factura.documento_origen_id)
                if factura.documento_origen_id else None
            )
            tipo = factura.tipo_documento
            if tipo not in TIPO.values:
                raise DocumentoInvalido("El tipo de documento no admite confirmación.")
            lineas_referencia = (
                (detalle.articulo_id, detalle.cantidad, detalle.costo_unitario_neto_final) for detalle in detalles
            )
            _validar_referencia(tipo, factura.proveedor_id, origen, lineas_referencia, factura.iva)
            es_credito = tipo == TIPO.NOTA_CREDITO
            # Si existe una guía confirmada, la mercadería ya entró. Su factura puede actualizar el precio.
            mueve_stock = not (tipo == TIPO.FACTURA and origen)
            tipo_movimiento = MovimientoCardex.Tipo.REVERSA if es_credito else MovimientoCardex.Tipo.INGRESO
            # Bloqueamos los artículos en el mismo orden para que dos operaciones no queden esperando entre sí.
            articulos = {articulo.pk: articulo for articulo in Articulo.objects.select_for_update().filter(
                pk__in={detalle.articulo_id for detalle in detalles},
            ).order_by("pk")}
            movimientos = []
            documentos = []
            for detalle in detalles:
                articulo = articulos[detalle.articulo_id]
                # Solo una factura confirmada puede cambiar el precio de lista. Un campo vacío conserva el anterior.
                if tipo == TIPO.FACTURA and detalle.precio_lista is not None and detalle.precio_lista != articulo.precio_lista:
                    precio_anterior = articulo.precio_lista
                    articulo.precio_lista = detalle.precio_lista
                    articulo.save(update_fields=["precio_lista"])
                    BitacoraAuditoria.objects.create(
                        usuario=usuario, accion="COMPRA", entidad="Articulo", id_registro=articulo.pk,
                        valor_anterior={"precio_lista": str(precio_anterior) if precio_anterior is not None else None},
                        valor_nuevo={"precio_lista": str(detalle.precio_lista), "factura_id": factura.pk},
                        detalle=f"Precio de lista actualizado con el documento {factura.folio}.",
                    )
                if not mueve_stock:
                    continue
                stock_anterior = articulo.stock_fisico
                articulo.stock_fisico += -detalle.cantidad if es_credito else detalle.cantidad
                if not 0 <= articulo.stock_fisico <= MAX_STOCK:
                    mensaje = (
                        "No hay stock suficiente para aplicar la nota de crédito."
                        if es_credito else "El ingreso supera el stock máximo admitido."
                    )
                    raise StockFueraDeRango(mensaje)
                campos_articulo = ["stock_fisico"]
                if not es_credito and tipo in (TIPO.FACTURA, TIPO.BOLETA) and detalle.costo_unitario_neto_final > 0:
                    articulo.actualizar_precio(
                        detalle.costo_unitario_neto_final,
                        cantidad_ingresada=detalle.cantidad,
                        fecha=factura.fecha_emision,
                    )
                    campos_articulo.extend(["costo_promedio", "ultimo_costo_compra", "fecha_actualizacion_precio"])
                articulo.save(update_fields=campos_articulo)
                # La nota deja un movimiento nuevo que explica la rebaja. La factura original queda como respaldo.
                justificacion = f"Nota de crédito sobre factura {origen.folio}." if es_credito else None
                movimiento = MovimientoCardex.objects.create(
                    tipo=tipo_movimiento, articulo=articulo, usuario=usuario,
                    cantidad=detalle.cantidad, stock_resultante=articulo.stock_fisico,
                    costo_unitario=detalle.costo_unitario_neto_final,
                    proveedor_id=factura.proveedor_id,
                    documento_referencia=f"{factura.get_tipo_documento_display()} {factura.folio}",
                    justificacion=justificacion,
                )
                BitacoraAuditoria.objects.create(
                    usuario=usuario, accion="CREAR", entidad="MovimientoCardex", id_registro=movimiento.pk,
                    valor_anterior={"stock_fisico": stock_anterior}, valor_nuevo={
                        "stock_fisico": articulo.stock_fisico, "factura_id": factura.pk,
                        "detalle_factura_id": detalle.pk, "proveedor_id": factura.proveedor_id,
                        "articulo_id": articulo.pk, "cantidad": detalle.cantidad,
                    }, detalle=f"{factura.get_tipo_documento_display()} {factura.folio} confirmada en inventario.",
                )
                movimientos.append({
                    "id": movimiento.pk, "tipo": movimiento.tipo, "articulo_id": articulo.pk,
                    "usuario_id": usuario.pk, "cantidad": movimiento.cantidad,
                    "stock_resultante": movimiento.stock_resultante, "obra_id": None,
                    "fecha_hora": movimiento.fecha_hora.isoformat(), "justificacion": justificacion,
                    "factura_id": factura.pk, "detalle_factura_id": detalle.pk, "proveedor_id": factura.proveedor_id,
                })
                documentos.append(documento_cardex(
                    movimiento, operacion="nota_credito" if es_credito else "ingreso_factura",
                    factura_id=factura.pk, detalle_factura_id=detalle.pk, proveedor_id=factura.proveedor_id,
                    tipo_documento=tipo, documento_origen_id=factura.documento_origen_id,
                ))
            if tipo != TIPO.FACTURA or origen:
                BitacoraAuditoria.objects.create(
                    usuario=usuario, accion="COMPRA", entidad="FacturaCompra", id_registro=factura.pk,
                    valor_anterior={"estado_ingreso": ESTADO.PENDIENTE},
                    valor_nuevo={
                        "estado_ingreso": ESTADO.CONFIRMADO,
                        "tipo_documento": tipo,
                        "documento_origen_id": factura.documento_origen_id,
                        "total": str(-factura.total if es_credito else factura.total),
                    },
                    detalle=f"{factura.get_tipo_documento_display()} {factura.folio} confirmada.",
                )
            factura.estado_ingreso = FacturaCompra.EstadoIngreso.CONFIRMADO
            factura.save(update_fields=["estado_ingreso"])
            if documentos:
                # Enviamos el respaldo a MongoDB después de que PostgreSQL guarde el movimiento.
                transaction.on_commit(lambda: guardar_respaldo_cardex(documentos), robust=True)
    except FacturaCompra.DoesNotExist:
        return error_json("FACTURA_NO_ENCONTRADA", "La factura no existe.", 404)
    except StockFueraDeRango as error:
        return error_json("STOCK_FUERA_DE_RANGO", str(error), 409)
    except DocumentoInvalido as error:
        return error_json("DOCUMENTO_INVALIDO", str(error), 409)
    except DatabaseError:
        logger.exception("Falló la transacción de ingreso de materiales.")
        return error_json("ERROR_INGRESO", "No se pudo confirmar el ingreso. Vuelve a intentarlo.", 500)

    return JsonResponse({"factura_id": factura.pk, "movimientos": movimientos}, status=201)



class RegistrarCompraForm(EncabezadoFacturaForm):
    proveedor_id = forms.ModelChoiceField(
        label="Proveedor", queryset=EntidadComercial.proveedores().order_by("razon_social"),
    )
    tipo_documento = forms.ChoiceField(choices=TIPO.choices, initial=TIPO.FACTURA, required=False)
    documento_origen_id = forms.ModelChoiceField(
        label="Documento de origen", required=False,
        queryset=FacturaCompra.objects.filter(
            estado_ingreso=ESTADO.CONFIRMADO, tipo_documento__in=(TIPO.GUIA, TIPO.FACTURA),
        ).select_related("proveedor").order_by("-fecha_emision", "-pk"),
        help_text="Selecciona la guía si ya recibiste los artículos o la factura que corrige una nota de crédito.",
    )
    fecha_emision = forms.DateField(label="Fecha de emisión", widget=forms.DateInput(attrs={"type": "date"}))


class DetalleCompraForm(DetalleFacturaForm):
    articulo_id = forms.ModelChoiceField(
        label="Artículo", queryset=Articulo.objects.only("sku", "descripcion").order_by("sku"),
    )
    precio_lista = forms.DecimalField(
        label="Nuevo precio de lista", required=False, min_value=0, max_digits=12, decimal_places=2,
        help_text="Opcional. Si lo dejas vacío se conserva el precio actual.",
    )


# Django recibe un grupo de campos por cada artículo y comprueba cuántas líneas se enviaron.
LineasCompra = forms.formset_factory(
    DetalleCompraForm, min_num=1, extra=0, max_num=MAX_LINEAS,
    validate_min=True, validate_max=True, absolute_max=MAX_LINEAS,
)


def _lineas_web(request):
    if request.method != "POST":
        return LineasCompra(prefix="detalles")
    datos = request.POST
    if "detalles-TOTAL_FORMS" not in datos and "articulo_id" in datos:
        # Conserva el envío desde formularios abiertos antes de la actualización.
        datos = datos.copy()
        datos.update({"detalles-TOTAL_FORMS": "1", "detalles-INITIAL_FORMS": "0"})
        for campo in DetalleCompraForm.base_fields:
            if campo in datos:
                datos[f"detalles-0-{campo}"] = datos[campo]
    return LineasCompra(datos, prefix="detalles")


@facultad_requerida("registrar_factura")
def vista_registrar_compra(request):
    formulario = RegistrarCompraForm(request.POST if request.method == "POST" else None)
    lineas = _lineas_web(request)
    if request.method == "POST" and formulario.is_valid() and lineas.is_valid():
        origen = formulario.cleaned_data["documento_origen_id"]
        datos = {
            **formulario.cleaned_data,
            "proveedor_id": formulario.cleaned_data["proveedor_id"].pk,
            "documento_origen_id": origen.pk if origen else None,
            "fecha_emision": formulario.cleaned_data["fecha_emision"].isoformat(),
            "detalles": [
                {**linea, "articulo_id": linea["articulo_id"].pk} for linea in lineas.cleaned_data if linea
            ],
        }
        # Usamos la misma validación que la API y devolvemos cada error junto al campo que lo causó.
        resultado = _registrar_factura_datos(request.user, datos)
        if resultado.status_code == 201:
            factura = json.loads(resultado.content)
            messages.success(request, f"Documento registrado por $ {factura['total']}. Queda pendiente de confirmación.")
            return redirect("compras")
        datos_error = json.loads(resultado.content)
        formulario.add_error(None, datos_error["mensaje"])
        for ruta, errores in datos_error["campos"].items():
            for mensaje in errores:
                if ruta.startswith("detalles."):
                    _, indice, campo = ruta.split(".")
                    lineas[int(indice)].add_error(campo, mensaje)
                else:
                    formulario.add_error(ruta, mensaje)
    return render(request, "compras/registrar.html", {
        "formulario": formulario, "lineas": lineas, "tasa_iva": TASA_IVA,
    })


@facultad_requerida("confirmar_ingreso")
@require_POST
def vista_confirmar_compra(request, factura_id):
    resultado = _confirmar_ingreso_datos(request.user, {"factura_id": factura_id})
    if resultado.status_code == 201:
        messages.success(request, "Documento confirmado.")
    else:
        messages.error(request, json.loads(resultado.content)["mensaje"])
    return redirect("compras")


@facultad_requerida("ver_costos")
@require_GET
def exportar_precios_excel(request, pk):
    from openpyxl import Workbook

    articulo = get_object_or_404(Articulo, pk=pk)
    libro = Workbook()
    hoja = libro.active
    hoja.title = "Precios"
    hoja.append(["Fecha", "Factura", "Proveedor", "Precio unitario", "Descuentos", "Precio neto final"])
    for detalle in ultimas_compras(articulo.pk).select_related("factura__proveedor"):
        hoja.append([
            detalle.factura.fecha_emision, detalle.factura.folio, detalle.factura.proveedor.razon_social,
            detalle.precio_unitario_neto,
            f"{detalle.descuento_1}% / {detalle.descuento_2}% / {detalle.descuento_3}%",
            detalle.costo_unitario_neto_final,
        ])
        for columna in (2, 3):
            # Folios y proveedores se exportan como texto aunque empiecen con '='.
            hoja.cell(hoja.max_row, columna).data_type = "s"
    respuesta = HttpResponse(content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    respuesta["Content-Disposition"] = f'attachment; filename="precios_{articulo.pk}.xlsx"'
    libro.save(respuesta)
    return respuesta
