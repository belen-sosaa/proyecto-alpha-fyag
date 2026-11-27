import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import DatabaseError, transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from apps.bodega.models import Articulo, BitacoraAuditoria, MovimientoCardex, Obra
from apps.bodega.respaldo import documento_cardex, guardar_respaldo_cardex
from apps.bodega.views import contexto_operativo
from apps.cuentas.decorators import facultad_requerida
from apps.cuentas.roles import ADMIN_BODEGA
from .forms import RecepcionDespachoForm
from .models import Despacho, RecepcionDespacho


logger = logging.getLogger(__name__)


@login_required
def vista_despacho(request):
    contexto = contexto_operativo(request.user)
    if "despacho" not in contexto["modulos_operativos"]:
        return redirect("bodega_web")
    modulo = contexto["modulos_operativos"]["despacho"]
    contexto.update(modulo_actual=modulo, titulo_panel=modulo["nombre"])
    despachos = _despachos_visibles(request.user, contexto).order_by("-fecha_despacho", "-pk")
    pagina = Paginator(despachos, 10).get_page(request.GET.get("pagina"))
    contexto.update(pagina=pagina, despachos_recientes=pagina.object_list)
    return render(request, "despachos/despacho.html", contexto)


def _despachos_visibles(usuario, contexto):
    despachos = Despacho.objects.filter(tipo_movimiento__iexact="DESPACHO")
    if contexto["recepcion"]:
        despachos = despachos.filter(Q(recepcion__isnull=True) | Q(recepcion__receptor=usuario))
    elif contexto["rol_actual"] != ADMIN_BODEGA and not contexto["perm"]["consultar_despachos"]:
        despachos = despachos.filter(usuario=usuario)
    return despachos.select_related("articulo", "usuario", "obra", "recepcion").defer("recepcion__firma")


@login_required
def comprobante_despacho(request, despacho_id):
    contexto = contexto_operativo(request.user)
    if "despacho" not in contexto["modulos_operativos"]:
        raise PermissionDenied

    despacho = get_object_or_404(_despachos_visibles(request.user, contexto), pk=despacho_id)
    recepcion_firmada = getattr(despacho, "recepcion", None)
    contexto.update(
        comprobante=despacho,
        recepcion_firmada=recepcion_firmada,
        modulo_actual=contexto["modulos_operativos"]["despacho"],
        titulo_panel="Comprobante",
    )
    if recepcion_firmada:
        contexto["firma_trazos"] = [
            " ".join(f"{x},{y}" for x, y in (trazo if len(trazo) > 1 else trazo * 2))
            for trazo in recepcion_firmada.firma
        ]
    elif contexto["recepcion"]:
        contexto["form_firma"] = RecepcionDespachoForm()
    return render(request, "despachos/despacho.html", contexto)


@facultad_requerida("firmar_recepcion")
@require_POST
def registrar_recepcion(request, despacho_id):
    form = RecepcionDespachoForm(request.POST)
    if not form.is_valid():
        messages.error(request, form.errors["firma"][0])
        return redirect("comprobante_despacho", despacho_id=despacho_id)

    try:
        with transaction.atomic():
            # El bloqueo serializa dos intentos de firmar el mismo despacho.
            despacho = get_object_or_404(
                Despacho.objects.select_for_update(), pk=despacho_id,
                tipo_movimiento__iexact="DESPACHO",
            )
            previa = RecepcionDespacho.objects.filter(despacho=despacho).first()
            if previa:
                messages.info(request, "El despacho ya tiene una recepción firmada.")
                if previa.receptor_id != request.user.pk:
                    return redirect("despachos")
                return redirect("comprobante_despacho", despacho_id=despacho_id)

            recepcion = RecepcionDespacho.objects.create(
                despacho=despacho,
                receptor=request.user,
                nombre_receptor=request.user.get_full_name() or request.user.get_username(),
                firma=form.cleaned_data["firma"],
            )
            BitacoraAuditoria.objects.create(
                usuario=request.user, accion="CREAR", entidad="RecepcionDespacho",
                id_registro=recepcion.pk,
                valor_nuevo={"despacho_id": despacho.pk, "receptor_id": request.user.pk},
                detalle=f"Recepción del despacho {despacho.pk} firmada.",
            )
    except DatabaseError:
        logger.exception("Falló la transacción de recepción de despacho.")
        messages.error(request, "No se pudo registrar la recepción. Vuelve a intentarlo.")
    else:
        messages.success(request, "Recepción firmada correctamente.")
    return redirect("comprobante_despacho", despacho_id=despacho_id)


# La misma facultad que protege el formulario autoriza esta consulta.
@facultad_requerida("despachar")
@require_GET
def articulo_por_codigo(request):
    """Busca un artículo para seleccionarlo en un despacho."""
    codigo = request.GET.get("codigo", "").strip()
    if not codigo or len(codigo) > Articulo._meta.get_field("codigo_barras").max_length:
        return JsonResponse({"error": "Ingresa un código de barras válido."}, status=400)

    # Los identificadores alfanuméricos del catálogo se comparan sin distinguir mayúsculas.
    filtro = Q(codigo_barras__iexact=codigo)
    formato = request.GET.get("formato")
    if codigo.isascii() and codigo.isdecimal():
        if formato == "UPC_A" and len(codigo) == 12:
            filtro |= Q(codigo_barras="0" + codigo)
        elif formato == "EAN_13" and len(codigo) == 13 and codigo.startswith("0"):
            filtro |= Q(codigo_barras=codigo[1:])

    # Dos coincidencias bastan para detectar la ambigüedad y evitar seleccionar el insumo equivocado.
    coincidencias = list(Articulo.objects.filter(filtro).values(
        "id", "sku", "descripcion", "codigo_barras", "unidad_medida",
    )[:2])
    if not coincidencias:
        return JsonResponse({"error": "No encontramos un artículo con ese código."}, status=404)
    if len(coincidencias) > 1:
        return JsonResponse(
            {"error": "El código coincide con más de un artículo. Selecciona el insumo manualmente."},
            status=409,
        )
    return JsonResponse(coincidencias[0])


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
            obra = Obra.objects.select_for_update().get(pk=obra_id)
            articulo = Articulo.objects.select_for_update().get(pk=articulo_id)
            stock_anterior = articulo.stock_fisico
            if stock_anterior < cantidad:
                messages.error(request, "Stock insuficiente para realizar el despacho.")
                return redirect("despachos")
            if not obra.numero_presupuesto.strip() or not obra.tecnico_responsable.strip():
                messages.error(request, "La obra necesita un número de presupuesto y un técnico responsable.")
                return redirect("despachos")
            if articulo.precio_lista is None:
                messages.error(request, "El artículo necesita un precio de lista para confirmar el despacho.")
                return redirect("despachos")

            articulo.stock_fisico = stock_anterior - cantidad
            articulo.save(update_fields=["stock_fisico"])
            movimiento = MovimientoCardex.objects.create(
                tipo=MovimientoCardex.Tipo.DESPACHO, articulo=articulo, obra=obra,
                cantidad=cantidad, usuario=request.user, stock_resultante=articulo.stock_fisico,
            )
            despacho = Despacho.objects.create(
                articulo=articulo, obra=obra, cantidad=cantidad, usuario=request.user,
                mongodb_id=str(movimiento.pk),
                numero_presupuesto=obra.numero_presupuesto,
                presupuesto_aplicado=obra.presupuesto_asignado,
                tecnico_responsable=obra.tecnico_responsable,
                precio_unitario_aplicado=articulo.precio_lista,
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
            documento = documento_cardex(
                movimiento, operacion="despacho_material", despacho_id=despacho.pk,
                dispositivo=request.POST.get("dispositivo", "tablet_operativa"),
                geo_ubicacion={"latitud": obra.latitud, "longitud": obra.longitud},
                tag_iot=request.POST.get("tag_iot", ""), sku=articulo.sku,
                descripcion=articulo.descripcion, obra=obra.nombre,
                numero_presupuesto=despacho.numero_presupuesto,
                tecnico_responsable=despacho.tecnico_responsable,
                precio_unitario_aplicado=str(despacho.precio_unitario_aplicado),
            )
            # El respaldo se ejecuta solo si se confirma la transacción de inventario.
            transaction.on_commit(lambda: guardar_respaldo_cardex([documento]), robust=True)
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
