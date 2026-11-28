import json
import logging

from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import DatabaseError, transaction
from django.db.models import Count, F, Max, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.cuentas.decorators import facultad_requerida
from apps.cuentas.roles import puede
from .models import Articulo, BitacoraAuditoria, ConteoInventario, DetalleConteoInventario, MovimientoCardex
from .respaldo import documento_cardex, guardar_respaldo_cardex


logger = logging.getLogger(__name__)
MAX_ARTICULOS = 50
MAX_STOCK = 2**31 - 1
ERROR_GUARDAR_CONTEO = "No se pudo guardar el conteo. Vuelve a intentarlo."
# La comparación usa el saldo que quedó guardado al contar, aunque el stock cambie después.
DISCREPANCIA = ~Q(stock_fisico=F("stock_sistema"))


class LineaConteoForm(forms.Form):
    articulo = forms.ModelChoiceField(
        queryset=Articulo.objects.only("sku", "descripcion").order_by("sku"),
        label="Artículo", empty_label="Selecciona un artículo",
    )
    stock_fisico = forms.IntegerField(label="Cantidad contada", min_value=0, max_value=MAX_STOCK)


LineasConteo = forms.formset_factory(
    LineaConteoForm, min_num=1, extra=0, max_num=MAX_ARTICULOS,
    validate_min=True, validate_max=True, absolute_max=MAX_ARTICULOS,
)


class ObservacionConteoForm(forms.Form):
    observacion = forms.CharField(
        label="Observación", max_length=2000, required=False, widget=forms.Textarea(attrs={"rows": 3}),
    )


def guardar_conteo(usuario, lineas, observacion, bases=None):
    # El conteo deja evidencia de lo que había en bodega. Los ajustes de inventario se hacen por separado.
    cantidades = dict(lineas)
    if len(cantidades) != len(lineas):
        raise ValidationError("Ingresa cada artículo una sola vez por conteo.")

    with transaction.atomic():
        # Captura los saldos con el mismo bloqueo que usan los movimientos de stock.
        saldos = dict(
            Articulo.objects.select_for_update().filter(pk__in=cantidades)
            .order_by("pk").values_list("pk", "stock_fisico")
        )
        if len(saldos) != len(cantidades):
            raise ValidationError("Uno de los artículos del conteo ya no está disponible.")
        if bases is not None:
            ultimos_movimientos = dict(MovimientoCardex.objects.filter(articulo_id__in=cantidades).values(
                "articulo_id",
            ).annotate(ultimo=Max("pk")).values_list("articulo_id", "ultimo"))
            if any(bases[pk] != (saldo, ultimos_movimientos.get(pk)) for pk, saldo in saldos.items()):
                raise ValidationError(
                    "El inventario cambió desde la descarga. Actualiza los datos y realiza un nuevo conteo.",
                    code="conflicto",
                )
        conteo = ConteoInventario.objects.create(usuario=usuario, observacion=observacion)
        detalles = DetalleConteoInventario.objects.bulk_create([
            DetalleConteoInventario(
                conteo=conteo, articulo_id=articulo_id, stock_sistema=saldos[articulo_id],
                stock_fisico=cantidad,
            )
            for articulo_id, cantidad in lineas
        ])
        BitacoraAuditoria.objects.create(
            usuario=usuario, accion="CREAR", entidad="ConteoInventario", id_registro=conteo.pk,
            valor_nuevo={
                "articulos": len(detalles),
                "discrepancias": sum(detalle.diferencia != 0 for detalle in detalles),
            },
            detalle=f"Conteo físico #{conteo.pk} registrado.",
        )
    return conteo, detalles


@facultad_requerida("ver_conteos")
@require_GET
def vista_conteos(request):
    conteos = ConteoInventario.objects.select_related("usuario").annotate(
        discrepancias=Count("detalles", filter=~Q(detalles__stock_fisico=F("detalles__stock_sistema"))),
    ).order_by("-fecha_hora", "-pk")
    return render(request, "bodega/conteos.html", {
        "pagina": Paginator(conteos, 15).get_page(request.GET.get("pagina")),
        "puede_registrar": puede(request.user, "registrar_conteo"),
    })


@facultad_requerida("registrar_conteo")
@require_http_methods(["GET", "POST"])
def nuevo_conteo(request):
    formulario = LineasConteo(request.POST if request.method == "POST" else None)
    observacion_form = ObservacionConteoForm(request.POST if request.method == "POST" else None)
    if formulario.is_valid() and observacion_form.is_valid():
        lineas = [
            (datos["articulo"].pk, datos["stock_fisico"])
            for datos in formulario.cleaned_data if datos
        ]
        try:
            conteo, _ = guardar_conteo(request.user, lineas, observacion_form.cleaned_data["observacion"])
        except ValidationError as error:
            messages.error(request, error.message)
        except DatabaseError:
            logger.exception("Falló el registro de conteo.")
            messages.error(request, ERROR_GUARDAR_CONTEO)
        else:
            messages.success(request, "Conteo guardado correctamente.")
            return redirect("discrepancias_conteo", conteo_id=conteo.pk)
    return render(request, "bodega/conteos.html", {
        "formulario": formulario, "observacion_form": observacion_form,
    })


@facultad_requerida("ver_conteos")
@require_GET
def vista_discrepancias(request, conteo_id):
    conteo = get_object_or_404(ConteoInventario.objects.select_related("usuario"), pk=conteo_id)
    # El reporte enfoca la revisión en las diferencias. Todas las líneas del conteo siguen guardadas.
    detalles = conteo.detalles.filter(DISCREPANCIA).select_related("articulo").order_by("pk")
    return render(request, "bodega/conteos.html", {
        "conteo": conteo,
        "detalles": detalles,
        "puede_finalizar": puede(request.user, "finalizar_conteo") and conteo.estado == ConteoInventario.Estado.EN_PROCESO,
    })


def _linea_json(detalle):
    return {
        "conteo_id": detalle.conteo_id,
        "articulo_id": detalle.articulo_id,
        "stock_sistema": detalle.stock_sistema,
        "stock_fisico": detalle.stock_fisico,
        "diferencia": detalle.diferencia,
    }


def datos_conteo(datos, *, con_base=False):
    detalles = datos.get("detalles") if isinstance(datos, dict) else None
    if not isinstance(detalles, list) or not 1 <= len(detalles) <= MAX_ARTICULOS:
        raise ValidationError(f"Ingresa entre 1 y {MAX_ARTICULOS} artículos.")

    observacion = datos.get("observacion", "")
    if not isinstance(observacion, str) or "\x00" in observacion or len(observacion) > 2000:
        raise ValidationError("La observación debe ser texto válido.")

    lineas = []
    bases = {} if con_base else None
    for detalle in detalles:
        if not isinstance(detalle, dict):
            raise ValidationError("Cada línea debe incluir un artículo y una cantidad contada.")
        articulo = detalle.get("articulo_id")
        cantidad = detalle.get("stock_fisico")
        if type(articulo) is not int or not 1 <= articulo <= 2**63 - 1:
            raise ValidationError("El identificador de artículo no es válido.")
        if type(cantidad) is not int or not 0 <= cantidad <= MAX_STOCK:
            raise ValidationError("La cantidad contada debe ser un entero no negativo dentro del rango de stock.")
        if con_base:
            saldo = detalle.get("stock_sistema")
            cursor = detalle.get("ultimo_movimiento")
            if (
                type(saldo) is not int or not 0 <= saldo <= MAX_STOCK
                or "ultimo_movimiento" not in detalle
                or (cursor is not None and (type(cursor) is not int or not 1 <= cursor <= 2**63 - 1))
            ):
                raise ValidationError("Falta la base de inventario. Actualiza los datos para contar.")
            bases[articulo] = (saldo, cursor)
        lineas.append((articulo, cantidad))
    return lineas, observacion.strip(), bases


@facultad_requerida("registrar_conteo")
@require_POST
def registrar_conteo(request):
    try:
        try:
            datos = json.loads(request.body)
        except (ValueError, RecursionError) as error:
            raise ValidationError("Envía un objeto JSON válido.") from error
        conteo, detalles = guardar_conteo(request.user, *datos_conteo(datos))
    except ValidationError as error:
        return JsonResponse({
            "codigo": "CONTEO_INVALIDO", "mensaje": error.message, "campos": {},
        }, status=400)
    except DatabaseError:
        logger.exception("Falló el registro de conteo.")
        return JsonResponse({
            "codigo": "ERROR_CONTEO", "mensaje": ERROR_GUARDAR_CONTEO, "campos": {},
        }, status=500)
    return JsonResponse({
        "id": conteo.pk, "usuario_id": conteo.usuario_id, "fecha_hora": conteo.fecha_hora,
        "observacion": conteo.observacion,
        "detalles": [_linea_json(detalle) for detalle in detalles],
    }, status=201)


@facultad_requerida("ver_conteos")
@require_GET
def consultar_discrepancias(request, conteo_id):
    conteo = get_object_or_404(ConteoInventario, pk=conteo_id)
    detalles = conteo.detalles.filter(DISCREPANCIA).order_by("pk")
    return JsonResponse([_linea_json(detalle) for detalle in detalles], safe=False)


@transaction.atomic
def aplicar_conteo(usuario, conteo_id):
    conteo = get_object_or_404(ConteoInventario.objects.select_for_update(), pk=conteo_id)
    if conteo.estado == ConteoInventario.Estado.FINALIZADO:
        return False

    detalles = {detalle.articulo_id: detalle for detalle in conteo.detalles.order_by("articulo_id")}
    articulos = list(Articulo.objects.select_for_update().filter(pk__in=detalles).order_by("pk"))
    if any(articulo.stock_fisico != detalles[articulo.pk].stock_sistema for articulo in articulos) or (
        MovimientoCardex.objects.filter(articulo_id__in=detalles, fecha_hora__gte=conteo.fecha_hora).exists()
    ):
        raise ValidationError("El inventario cambió después de este conteo. Registra un nuevo conteo antes de ajustar.")

    documentos = []
    for articulo in articulos:
        detalle = detalles[articulo.pk]
        if not detalle.diferencia:
            continue
        articulo.stock_fisico = detalle.stock_fisico
        articulo.save(update_fields=["stock_fisico"])
        movimiento = MovimientoCardex.objects.create(
            tipo=MovimientoCardex.Tipo.AJUSTE, articulo=articulo, usuario=usuario,
            conteo_origen=conteo, cantidad=abs(detalle.diferencia), variacion_stock=detalle.diferencia,
            stock_resultante=articulo.stock_fisico, justificacion=f"Ajuste por conteo físico #{conteo.pk}.",
        )
        BitacoraAuditoria.objects.create(
            usuario=usuario, accion="CREAR", entidad="MovimientoCardex", id_registro=movimiento.pk,
            valor_anterior={"stock_fisico": detalle.stock_sistema},
            valor_nuevo={
                "tipo": movimiento.tipo, "conteo_id": conteo.pk, "articulo_id": articulo.pk,
                "stock_fisico": articulo.stock_fisico, "variacion_stock": detalle.diferencia,
            },
            detalle=movimiento.justificacion,
        )
        documentos.append(documento_cardex(
            movimiento, operacion="ajuste_conteo", conteo_id=conteo.pk, variacion_stock=detalle.diferencia,
        ))

    conteo.estado = ConteoInventario.Estado.FINALIZADO
    conteo.save(update_fields=["estado"])
    BitacoraAuditoria.objects.create(
        usuario=usuario, accion="FINALIZAR", entidad="ConteoInventario", id_registro=conteo.pk,
        valor_anterior={"estado": ConteoInventario.Estado.EN_PROCESO},
        valor_nuevo={"estado": conteo.estado, "ajustes": len(documentos)},
        detalle=f"Conteo físico #{conteo.pk} finalizado.",
    )
    if documentos:
        transaction.on_commit(lambda: guardar_respaldo_cardex(documentos), robust=True)
    return True


@facultad_requerida("finalizar_conteo")
@require_POST
def finalizar_conteo(request, conteo_id):
    try:
        aplicado = aplicar_conteo(request.user, conteo_id)
    except ValidationError as error:
        messages.error(request, error.message)
    except DatabaseError:
        logger.exception("Falló el ajuste por conteo.")
        messages.error(request, "No se pudo finalizar el conteo. Vuelve a intentarlo.")
    else:
        if aplicado:
            messages.success(request, "Conteo finalizado. Las diferencias se aplicaron al inventario.")
        else:
            messages.info(request, "El conteo ya estaba finalizado.")
    return redirect("discrepancias_conteo", conteo_id=conteo_id)
