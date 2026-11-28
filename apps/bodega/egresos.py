import json
import logging

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import DatabaseError, transaction
from django.db.models import F, Q, Sum
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from apps.compras.models import EntidadComercial
from apps.cuentas.decorators import facultad_requerida
from apps.cuentas.roles import puede
from apps.despachos.models import Despacho
from apps.despachos.preparacion import ultimo_costo_material
from .models import Articulo, BitacoraAuditoria, MovimientoCardex, Obra
from .respaldo import documento_cardex, guardar_respaldo_cardex


logger = logging.getLogger(__name__)
ERROR_GUARDADO = "No se pudo registrar el egreso. Vuelve a intentarlo."


class StockInsuficiente(Exception):
    pass


class MermaForm(forms.Form):
    articulo_id = forms.ModelChoiceField(
        queryset=Articulo.objects.order_by("sku"), label="Artículo",
        empty_label="Selecciona un artículo",
    )
    cantidad = forms.IntegerField(min_value=1, max_value=2**31 - 1, label="Cantidad")
    justificacion = forms.CharField(
        label="Motivo", max_length=2000,
        widget=forms.Textarea(attrs={"rows": 3}),
        error_messages={"required": "La justificación es obligatoria."},
    )


class DevolucionForm(MermaForm):
    proveedor_id = forms.ModelChoiceField(
        queryset=EntidadComercial.proveedores().order_by("razon_social", "pk"),
        label="Proveedor", empty_label="Selecciona un proveedor",
    )
    documento_referencia = forms.CharField(
        label="Documento asociado (opcional)", max_length=100, required=False,
    )


class UsoInternoForm(MermaForm):
    destino = forms.CharField(max_length=200, label="Destino del material")
    solicitante_responsable = forms.CharField(max_length=200, label="Solicitante responsable")
    justificacion = forms.CharField(
        required=False, max_length=2000, label="Observaciones", widget=forms.Textarea(attrs={"rows": 3}),
    )
    field_order = ["articulo_id", "cantidad", "destino", "solicitante_responsable", "justificacion"]


EGRESOS = {
    MovimientoCardex.Tipo.MERMA: ("registrar_merma", MermaForm, "Registrar merma"),
    MovimientoCardex.Tipo.DEVOLUCION: ("registrar_devolucion", DevolucionForm, "Registrar devolución"),
    MovimientoCardex.Tipo.USO_INTERNO: ("registrar_uso_interno", UsoInternoForm, "Registrar uso interno"),
}


def _guardar_egreso(usuario, tipo, datos):
    cantidad = datos["cantidad"]
    proveedor = datos.get("proveedor_id")
    with transaction.atomic():
        # Lee el saldo bajo bloqueo para evitar que dos salidas descuenten el mismo stock.
        articulo = Articulo.objects.select_for_update().get(pk=datos["articulo_id"].pk)
        stock_anterior = articulo.stock_fisico
        if cantidad > stock_anterior:
            raise StockInsuficiente("La cantidad supera el stock disponible.")
        if proveedor:
            proveedor = EntidadComercial.proveedores().get(pk=proveedor.pk)
        articulo.stock_fisico -= cantidad
        articulo.save(update_fields=["stock_fisico"])
        movimiento = MovimientoCardex.objects.create(
            tipo=tipo, articulo=articulo, cantidad=cantidad, usuario=usuario,
            proveedor=proveedor, stock_resultante=articulo.stock_fisico,
            justificacion=datos["justificacion"], documento_referencia=datos.get("documento_referencia", ""),
            destino=datos.get("destino", ""), solicitante_responsable=datos.get("solicitante_responsable", ""),
            variacion_stock=-cantidad,
        )
        uso_interno = {}
        detalle = movimiento.justificacion
        if tipo == MovimientoCardex.Tipo.USO_INTERNO:
            uso_interno = {"destino": movimiento.destino, "solicitante_responsable": movimiento.solicitante_responsable}
            detalle = f"Uso interno en {movimiento.destino}. Solicitado por {movimiento.solicitante_responsable}."
            if movimiento.justificacion:
                detalle += f" {movimiento.justificacion}"
        BitacoraAuditoria.objects.create(
            usuario=usuario, accion="CREAR", entidad="MovimientoCardex", id_registro=movimiento.pk,
            valor_anterior={"stock_fisico": stock_anterior},
            valor_nuevo={
                "tipo": tipo, "articulo_id": articulo.pk, "cantidad": cantidad,
                "proveedor_id": movimiento.proveedor_id, "stock_fisico": articulo.stock_fisico,
                "documento_referencia": movimiento.documento_referencia,
                **uso_interno,
            },
            detalle=detalle,
        )
        respaldo = documento_cardex(
            movimiento, operacion=tipo.lower(), sku=articulo.sku,
            documento_referencia=movimiento.documento_referencia,
            **uso_interno,
        )
        transaction.on_commit(lambda: guardar_respaldo_cardex([respaldo]), robust=True)
    return movimiento


@login_required
@require_http_methods(["GET", "POST"])
def vista_egreso(request, tipo):
    facultad, formulario, titulo = EGRESOS[tipo]
    if not puede(request.user, facultad):
        raise PermissionDenied
    form = formulario(request.POST if request.method == "POST" else None)
    if form.is_valid():
        try:
            movimiento = _guardar_egreso(request.user, tipo, form.cleaned_data)
        except StockInsuficiente as error:
            form.add_error("cantidad", str(error))
        except Articulo.DoesNotExist:
            form.add_error("articulo_id", "El artículo ya no está disponible.")
        except EntidadComercial.DoesNotExist:
            form.add_error("proveedor_id", "El proveedor ya no está disponible.")
        except DatabaseError:
            logger.exception("Falló la transacción de %s.", tipo)
            messages.error(request, ERROR_GUARDADO)
        else:
            messages.success(request, f"{movimiento.get_tipo_display()} registrada. Stock disponible: {movimiento.stock_resultante}.")
            return redirect("bodega_web")
    contexto = {"form": form, "titulo": titulo}
    if tipo == MovimientoCardex.Tipo.DEVOLUCION:
        devoluciones = MovimientoCardex.objects.filter(tipo=tipo).select_related(
            "articulo", "proveedor",
        ).order_by("-fecha_hora", "-pk")
        contexto["devoluciones"] = Paginator(devoluciones, 20).get_page(request.GET.get("pagina"))
    return render(request, "bodega/egreso.html", contexto)


def _error_json(codigo, mensaje, estado, campos=None):
    return JsonResponse({"codigo": codigo, "mensaje": mensaje, "campos": campos or {}}, status=estado)


def _datos_json(request, tipo):
    try:
        datos = json.loads(request.body)
    except (ValueError, RecursionError) as error:
        raise ValidationError("Envía un objeto JSON válido.") from error
    if not isinstance(datos, dict):
        raise ValidationError("Envía un objeto JSON válido.")
    enteros = {"articulo_id", "cantidad"}
    if tipo == MovimientoCardex.Tipo.DEVOLUCION:
        enteros.add("proveedor_id")
    for campo in enteros & datos.keys():
        if type(datos[campo]) is not int or not 1 <= datos[campo] <= 2**63 - 1:
            raise ValidationError(f"{campo} debe ser un entero positivo.")
    for campo in {"justificacion", "documento_referencia", "destino", "solicitante_responsable"} & datos.keys():
        if not isinstance(datos[campo], str):
            raise ValidationError(f"{campo} debe ser texto.")
    return datos


@require_POST
def registrar_egreso(request, tipo):
    facultad, formulario, _ = EGRESOS[tipo]
    if not request.user.is_authenticated:
        return _error_json("SESION_REQUERIDA", "Inicia sesión para continuar.", 401)
    if not puede(request.user, facultad):
        return _error_json("SIN_PERMISO", "Tu rol no permite esta operación.", 403)
    try:
        form = formulario(_datos_json(request, tipo))
    except ValidationError as error:
        return _error_json("DATOS_INVALIDOS", error.message, 400)
    if not form.is_valid():
        return _error_json("DATOS_INVALIDOS", "Revisa los campos indicados.", 400, dict(form.errors))
    try:
        movimiento = _guardar_egreso(request.user, tipo, form.cleaned_data)
    except StockInsuficiente as error:
        return _error_json("STOCK_INSUFICIENTE", str(error), 409)
    except (Articulo.DoesNotExist, EntidadComercial.DoesNotExist):
        return _error_json("REFERENCIA_NO_DISPONIBLE", "El artículo o proveedor ya no está disponible.", 409)
    except DatabaseError:
        logger.exception("Falló la transacción de %s.", tipo)
        return _error_json("ERROR_EGRESO", ERROR_GUARDADO, 500)
    resultado = {
        "id": movimiento.pk, "tipo": movimiento.tipo, "articulo_id": movimiento.articulo_id,
        "usuario_id": movimiento.usuario_id, "proveedor_id": movimiento.proveedor_id,
        "obra_id": movimiento.obra_id, "fecha_hora": movimiento.fecha_hora,
        "cantidad": movimiento.cantidad, "stock_resultante": movimiento.stock_resultante,
        "justificacion": movimiento.justificacion, "documento_referencia": movimiento.documento_referencia,
        "despacho_id": None, "detalle_factura_id": None, "movimiento_original_id": None,
    }
    if tipo == MovimientoCardex.Tipo.USO_INTERNO:
        resultado.update(destino=movimiento.destino, solicitante_responsable=movimiento.solicitante_responsable)
    return JsonResponse(resultado, status=201)


def despachos_para_cambio():
    return Despacho.objects.filter(
        Q(tipo_movimiento__iexact="DESPACHO") | Q(tipo_movimiento__iexact="CAMBIO"),
        obra__estado=Obra.Estado.EN_PROCESO,
    ).annotate(devuelto=Sum("movimientocardex__cantidad", default=0)).filter(cantidad__gt=F("devuelto"))


class DespachoCambioField(forms.ModelChoiceField):
    def label_from_instance(self, despacho):
        return f"{despacho.obra} · {despacho.articulo} · #{despacho.pk} ({despacho.cantidad - despacho.devuelto} en obra)"


class CambioForm(MermaForm):
    despacho_id = DespachoCambioField(queryset=Despacho.objects.none(), label="Material en obra")
    articulo_id = forms.ModelChoiceField(queryset=Articulo.objects.order_by("sku"), label="Artículo de reemplazo")
    cantidad = forms.IntegerField(min_value=1, max_value=2**31 - 1, label="Cantidad que vuelve a bodega")
    cantidad_reemplazo = forms.IntegerField(min_value=1, max_value=2**31 - 1, label="Cantidad que sale a obra")
    field_order = ["despacho_id", "cantidad", "articulo_id", "cantidad_reemplazo", "justificacion"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["despacho_id"].queryset = despachos_para_cambio().select_related("obra", "articulo").order_by(
            "obra__nombre", "articulo__sku", "pk",
        )


def guardar_cambio(usuario, datos):
    referencia = datos["despacho_id"]
    reemplazo_id = datos["articulo_id"].pk
    cantidad = datos["cantidad"]
    cantidad_reemplazo = datos["cantidad_reemplazo"]
    motivo = datos["justificacion"]

    with transaction.atomic():
        obra = Obra.objects.select_for_update().get(pk=referencia.obra_id)
        despacho = Despacho.objects.select_for_update().get(pk=referencia.pk)
        if despacho.obra_id != obra.pk or despacho.tipo_movimiento.upper() not in {"DESPACHO", "CAMBIO"}:
            raise ValidationError("El material seleccionado ya no está disponible para cambiar en esa obra.")
        if despacho.articulo_id == reemplazo_id:
            raise ValidationError("Selecciona un artículo de reemplazo diferente.")
        disponibles = despacho.cantidad - MovimientoCardex.objects.filter(despacho_origen=despacho).aggregate(
            total=Sum("cantidad", default=0),
        )["total"]
        if obra.estado != Obra.Estado.EN_PROCESO or cantidad > disponibles:
            raise ValidationError("La obra no está en proceso o la cantidad supera lo que queda en obra.")
        articulos = Articulo.objects.select_for_update().filter(pk__in=[despacho.articulo_id, reemplazo_id]).order_by("pk")
        por_id = {articulo.pk: articulo for articulo in articulos}
        if len(por_id) != 2:
            raise ValidationError("Un artículo del cambio ya no está disponible.")
        original, reemplazo = por_id[despacho.articulo_id], por_id[reemplazo_id]
        if reemplazo.stock_fisico < cantidad_reemplazo or original.stock_fisico + cantidad > 2**31 - 1:
            raise ValidationError("El cambio supera las existencias o la capacidad de stock.")

        costo_reemplazo = ultimo_costo_material(reemplazo)
        entrada = None
        documentos = []
        for articulo, variacion, costo in (
            (original, cantidad, despacho.costo_unitario), (reemplazo, -cantidad_reemplazo, costo_reemplazo),
        ):
            stock_anterior = articulo.stock_fisico
            articulo.stock_fisico += variacion
            articulo.save(update_fields=["stock_fisico"])
            movimiento = MovimientoCardex.objects.create(
                tipo=MovimientoCardex.Tipo.CAMBIO, usuario=usuario, obra=obra, articulo=articulo,
                cantidad=abs(variacion), variacion_stock=variacion, stock_resultante=articulo.stock_fisico,
                costo_unitario=costo, justificacion=motivo, movimiento_original=entrada,
                despacho_origen=despacho if entrada is None else None,
            )
            BitacoraAuditoria.objects.create(
                usuario=usuario, accion="CREAR", entidad="MovimientoCardex", id_registro=movimiento.pk,
                valor_anterior={"stock_fisico": stock_anterior},
                valor_nuevo={
                    "tipo": movimiento.tipo, "articulo_id": articulo.pk, "obra_id": obra.pk,
                    "despacho_origen_id": despacho.pk, "cambio_id": entrada.pk if entrada else movimiento.pk,
                    "variacion_stock": variacion, "stock_fisico": articulo.stock_fisico,
                },
                detalle=f"Cambio de artículo en {obra.nombre}. {motivo}",
            )
            documentos.append(documento_cardex(
                movimiento, operacion="cambio_articulo", despacho_origen_id=despacho.pk,
                variacion_stock=variacion, cambio_id=entrada.pk if entrada else movimiento.pk,
            ))
            if entrada is None:
                entrada = movimiento

        Despacho.objects.create(
            obra=obra, articulo=reemplazo, cantidad=cantidad_reemplazo, usuario=usuario,
            tipo_movimiento="CAMBIO", costo_unitario=costo_reemplazo, mongodb_id=str(movimiento.pk),
        )
        obra.costo_materiales += cantidad_reemplazo * (costo_reemplazo or 0) - cantidad * (despacho.costo_unitario or 0)
        obra.save(update_fields=["costo_materiales"])
        transaction.on_commit(lambda: guardar_respaldo_cardex(documentos), robust=True)


@facultad_requerida("registrar_cambio")
@require_http_methods(["GET", "POST"])
def vista_cambio(request):
    form = CambioForm(request.POST if request.method == "POST" else None)
    if form.is_valid():
        try:
            guardar_cambio(request.user, form.cleaned_data)
        except ValidationError as error:
            form.add_error(None, error)
        except (Obra.DoesNotExist, Despacho.DoesNotExist):
            form.add_error("despacho_id", "El material en obra ya no está disponible.")
        except DatabaseError:
            logger.exception("Falló la transacción de cambio de artículos.")
            messages.error(request, "No se pudo registrar el cambio. Vuelve a intentarlo.")
        else:
            messages.success(request, "Cambio de artículos registrado.")
            return redirect("bodega_web")
    return render(request, "bodega/egreso.html", {"form": form, "titulo": "Cambiar artículos en obra"})
