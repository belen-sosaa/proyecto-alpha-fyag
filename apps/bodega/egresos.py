import json
import logging

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import DatabaseError, transaction
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from apps.compras.models import EntidadComercial
from apps.cuentas.roles import puede
from .models import Articulo, BitacoraAuditoria, MovimientoCardex
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


EGRESOS = {
    MovimientoCardex.Tipo.MERMA: ("registrar_merma", MermaForm, "Registrar merma"),
    MovimientoCardex.Tipo.DEVOLUCION: ("registrar_devolucion", DevolucionForm, "Registrar devolución"),
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
        )
        BitacoraAuditoria.objects.create(
            usuario=usuario, accion="CREAR", entidad="MovimientoCardex", id_registro=movimiento.pk,
            valor_anterior={"stock_fisico": stock_anterior},
            valor_nuevo={
                "tipo": tipo, "articulo_id": articulo.pk, "cantidad": cantidad,
                "proveedor_id": movimiento.proveedor_id, "stock_fisico": articulo.stock_fisico,
                "documento_referencia": movimiento.documento_referencia,
            },
            detalle=movimiento.justificacion,
        )
        respaldo = documento_cardex(
            movimiento, operacion=tipo.lower(), sku=articulo.sku,
            documento_referencia=movimiento.documento_referencia,
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
    return render(request, "bodega/egreso.html", {"form": form, "titulo": titulo})


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
    for campo in {"justificacion", "documento_referencia"} & datos.keys():
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
    return JsonResponse({
        "id": movimiento.pk, "tipo": movimiento.tipo, "articulo_id": movimiento.articulo_id,
        "usuario_id": movimiento.usuario_id, "proveedor_id": movimiento.proveedor_id,
        "obra_id": movimiento.obra_id, "fecha_hora": movimiento.fecha_hora,
        "cantidad": movimiento.cantidad, "stock_resultante": movimiento.stock_resultante,
        "justificacion": movimiento.justificacion, "documento_referencia": movimiento.documento_referencia,
        "despacho_id": None, "detalle_factura_id": None, "movimiento_original_id": None,
    }, status=201)
