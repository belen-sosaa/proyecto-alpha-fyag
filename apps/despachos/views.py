import logging
import uuid

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import DatabaseError, transaction
from django.http import JsonResponse
from django.shortcuts import redirect, render

from apps.bodega.models import Articulo, BitacoraAuditoria, MovimientoCardex, Obra
from apps.bodega.respaldo import guardar_respaldo_simulado
from apps.bodega.views import contexto_operativo
from apps.cuentas.decorators import facultad_requerida
from apps.cuentas.roles import ADMIN_BODEGA
from .models import Despacho


logger = logging.getLogger(__name__)


@login_required
def vista_despacho(request):
    contexto = contexto_operativo(request.user)
    if contexto["perm"]["despachar"]:
        despachos = Despacho.objects.filter(tipo_movimiento__iexact="DESPACHO")
        if contexto["rol_actual"] != ADMIN_BODEGA:
            despachos = despachos.filter(usuario=request.user)
        contexto["despachos_recientes"] = (
            despachos.select_related("articulo", "usuario").prefetch_related("obra")
            .order_by("-fecha_despacho", "-pk")[:10]
        )
    elif not contexto["recepcion"]:
        return redirect("bodega_web")
    return render(request, "despachos/despacho.html", contexto)


@facultad_requerida("despachar")
def procesar_despacho(request):
    if request.method != "POST":
        return redirect("despachos")
    obra_id = request.POST.get("obra")
    articulo_id = request.POST.get("articulo")
    if not obra_id or not articulo_id:
        messages.error(request, "Debe seleccionar una obra y un artículo.")
        return redirect("despachos")
    try:
        cantidad = int(request.POST.get("cantidad", "0"))
    except (TypeError, ValueError):
        messages.error(request, "La cantidad ingresada no es válida.")
        return redirect("despachos")
    if cantidad <= 0:
        messages.error(request, "La cantidad debe ser mayor que cero.")
        return redirect("despachos")
    try:
        obra_id = int(obra_id)
        articulo_id = int(articulo_id)
    except ValueError:
        messages.error(request, "La obra o el artículo seleccionado no existe.")
        return redirect("despachos")
    try:
        with transaction.atomic():
            obra = Obra.objects.get(pk=obra_id)
            articulo = Articulo.objects.select_for_update().get(pk=articulo_id)
            stock_anterior = articulo.stock_fisico
            if stock_anterior < cantidad:
                messages.error(request, "Stock insuficiente para realizar el despacho.")
                return redirect("despachos")

            articulo.stock_fisico = stock_anterior - cantidad
            articulo.save(update_fields=["stock_fisico"])
            despacho = Despacho.objects.create(
                articulo=articulo, obra=obra, cantidad=cantidad, usuario=request.user,
                mongodb_id=str(uuid.uuid4()),
            )
            movimiento = MovimientoCardex.objects.create(
                tipo=MovimientoCardex.Tipo.DESPACHO, articulo=articulo, obra=obra,
                cantidad=cantidad, usuario=request.user, stock_resultante=articulo.stock_fisico,
            )
            BitacoraAuditoria.objects.create(
                usuario=request.user, accion="CREAR", entidad="MovimientoCardex",
                id_registro=movimiento.pk, valor_anterior={"stock_fisico": stock_anterior},
                valor_nuevo={
                    "stock_fisico": articulo.stock_fisico, "despacho_id": despacho.pk,
                    "articulo_id": articulo.pk, "obra_id": obra.pk, "cantidad": cantidad,
                },
                detalle=f"Despacho {despacho.pk} confirmado.",
            )
            documento = {
                "_id": despacho.mongodb_id, "operacion": "despacho_material",
                "movimiento_id": movimiento.pk, "despacho_id": despacho.pk,
                "dispositivo": request.POST.get("dispositivo", "tablet_operativa"),
                "geo_ubicacion": {"latitud": obra.latitud, "longitud": obra.longitud},
                "tag_iot": request.POST.get("tag_iot", ""), "sku": articulo.sku,
                "descripcion": articulo.descripcion, "obra": obra.nombre, "cantidad": cantidad,
            }
            # El respaldo se ejecuta solo si se confirma la transacción de inventario.
            transaction.on_commit(lambda: guardar_respaldo_simulado([documento]), robust=True)
    except (Articulo.DoesNotExist, Obra.DoesNotExist):
        messages.error(request, "La obra o el artículo seleccionado no existe.")
        return redirect("despachos")
    except DatabaseError:
        logger.exception("Falló la transacción de despacho.")
        messages.error(request, "No se pudo registrar el despacho. Vuelve a intentarlo.")
        return redirect("despachos")
    messages.success(request, "Despacho registrado correctamente.")
    return redirect("despachos")


@facultad_requerida("despachar")
def obras_buscar(request):
    q = request.GET.get("q", "").strip()
    if len(q) < 3:
        return JsonResponse([], safe=False)
    obras = Obra.objects.filter(nombre__icontains=q).order_by("nombre", "id").values("id", "nombre")[:10]
    return JsonResponse(list(obras), safe=False)
