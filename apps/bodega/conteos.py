import json
import logging

from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import DatabaseError, transaction
from django.db.models import Count, F, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.cuentas.decorators import facultad_requerida
from apps.cuentas.roles import puede
from .models import Articulo, BitacoraAuditoria, ConteoInventario, DetalleConteoInventario


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
        label="Observación", required=False, widget=forms.Textarea(attrs={"rows": 3}),
    )


def _guardar_conteo(usuario, lineas, observacion):
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
            conteo, _ = _guardar_conteo(request.user, lineas, observacion_form.cleaned_data["observacion"])
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
    })


def _linea_json(detalle):
    return {
        "conteo_id": detalle.conteo_id,
        "articulo_id": detalle.articulo_id,
        "stock_sistema": detalle.stock_sistema,
        "stock_fisico": detalle.stock_fisico,
        "diferencia": detalle.diferencia,
    }


def _datos_conteo(request):
    try:
        datos = json.loads(request.body)
    except (ValueError, RecursionError) as error:
        raise ValidationError("Envía un objeto JSON válido.") from error

    detalles = datos.get("detalles") if isinstance(datos, dict) else None
    if not isinstance(detalles, list) or not 1 <= len(detalles) <= MAX_ARTICULOS:
        raise ValidationError(f"Ingresa entre 1 y {MAX_ARTICULOS} artículos.")

    observacion = datos.get("observacion", "")
    if not isinstance(observacion, str) or "\x00" in observacion:
        raise ValidationError("La observación debe ser texto válido.")

    lineas = []
    for detalle in detalles:
        if not isinstance(detalle, dict):
            raise ValidationError("Cada línea debe incluir un artículo y una cantidad contada.")
        articulo = detalle.get("articulo_id")
        cantidad = detalle.get("stock_fisico")
        if type(articulo) is not int or not 1 <= articulo <= 2**63 - 1:
            raise ValidationError("El identificador de artículo no es válido.")
        if type(cantidad) is not int or not 0 <= cantidad <= MAX_STOCK:
            raise ValidationError("La cantidad contada debe ser un entero no negativo dentro del rango de stock.")
        lineas.append((articulo, cantidad))
    return lineas, observacion.strip()


@facultad_requerida("registrar_conteo")
@require_POST
def registrar_conteo(request):
    try:
        lineas, observacion = _datos_conteo(request)
        conteo, detalles = _guardar_conteo(request.user, lineas, observacion)
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
