import json
import uuid
from pathlib import Path

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET

from apps.bodega.models import Articulo, Despacho, Obra
from apps.bodega.views import contexto_operativo
from apps.cuentas.decorators import facultad_requerida


@login_required
def vista_despachos(request):
    contexto = contexto_operativo(request.user)
    if not contexto["mostrar_despachos"]:
        raise PermissionDenied
    # La recepción se muestra al Supervisor. El selector se prepara solo para quien puede despachar.
    contexto["titulo_panel"] = "Recepción de despachos" if contexto["recepcion"] else "Despachos"
    if contexto["perm"]["despachar"]:
        contexto["articulos"] = Articulo.objects.order_by("sku")
    return render(request, "despachos/despachos.html", contexto)


# La misma facultad que protege el formulario autoriza esta consulta.
@facultad_requerida("despachar")
@require_GET
def articulo_por_codigo(request):
    """Busca un artículo para seleccionarlo en un despacho."""
    codigo = request.GET.get("codigo", "").strip()
    if not codigo or len(codigo) > Articulo._meta.get_field("codigo_barras").max_length:
        return JsonResponse({"error": "Ingresa un código de barras válido."}, status=400)

    # La entrada manual es exacta. La misma etiqueta escaneada puede leerse como UPC-A
    # de 12 dígitos o como EAN-13 con un cero inicial.
    codigos = [codigo]
    formato = request.GET.get("formato")
    if codigo.isascii() and codigo.isdecimal():
        if formato == "UPC_A" and len(codigo) == 12:
            codigos.append("0" + codigo)
        elif formato == "EAN_13" and len(codigo) == 13 and codigo.startswith("0"):
            codigos.append(codigo[1:])

    # Dos coincidencias bastan para detectar la ambigüedad y evitar seleccionar el insumo equivocado.
    coincidencias = list(Articulo.objects.filter(codigo_barras__in=codigos).values(
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
@transaction.atomic
def procesar_despacho(request):
    # El formulario envía los identificadores de la obra y el artículo más la cantidad solicitada.
    if request.method != "POST":
        return redirect("despachos_web")
    obra_id = request.POST.get("obra")
    articulo_id = request.POST.get("articulo")
    if not obra_id or not articulo_id:
        messages.error(request, "Debe seleccionar una obra y un artículo.")
        return redirect("despachos_web")
    try:
        cantidad = int(request.POST.get("cantidad", "0"))
    except (TypeError, ValueError):
        messages.error(request, "La cantidad ingresada no es válida.")
        return redirect("despachos_web")
    if cantidad <= 0:
        messages.error(request, "La cantidad debe ser mayor que cero.")
        return redirect("despachos_web")
    try:
        articulo = Articulo.objects.select_for_update().select_related("categoria").get(pk=int(articulo_id))
        obra = Obra.objects.get(pk=int(obra_id))
    except (Articulo.DoesNotExist, Obra.DoesNotExist, TypeError, ValueError):
        messages.error(request, "La obra o el artículo seleccionado no existe.")
        return redirect("despachos_web")
    # El artículo queda bloqueado hasta terminar la transacción. El stock se comprueba después de bloquearlo.
    if articulo.stock_fisico < cantidad:
        messages.error(request, "Stock insuficiente para realizar el despacho.")
        return redirect("despachos_web")

    despacho = Despacho(articulo=articulo, obra=obra, cantidad=cantidad, usuario=request.user)
    mongo_id = str(uuid.uuid4())
    despacho.mongodb_id = mongo_id
    mongo_document = {
        "_id": mongo_id, "operacion": "despacho_material",
        "dispositivo": request.POST.get("dispositivo", "tablet_operativa"),
        "geo_ubicacion": {"latitud": obra.latitud, "longitud": obra.longitud},
        "tag_iot": request.POST.get("tag_iot", ""), "sku": articulo.sku,
        "descripcion": articulo.descripcion, "obra": obra.nombre, "cantidad": cantidad,
    }
    # El Cardex de prueba se escribe en un JSON local. La transacción de Django cubre las escrituras de la base de datos.
    mock_file = Path(__file__).resolve().parent / "data" / "mock_mongodb_cardex.json"
    try:
        if mock_file.exists():
            with mock_file.open("r", encoding="utf-8") as archivo:
                try:
                    documentos = json.load(archivo)
                except json.JSONDecodeError:
                    documentos = []
                if not isinstance(documentos, list):
                    documentos = []
        else:
            documentos = []
        documentos.append(mongo_document)
        with mock_file.open("w", encoding="utf-8") as archivo:
            json.dump(documentos, archivo, ensure_ascii=False, indent=2)
    except OSError:
        messages.error(request, "No se pudo registrar el despacho en el almacenamiento local.")
        return redirect("despachos_web")
    # El despacho y el descuento quedan guardados juntos cuando termina la transacción.
    articulo.stock_fisico -= cantidad
    articulo.save(update_fields=["stock_fisico"])
    despacho.save()
    messages.success(request, "Despacho registrado correctamente.")
    return redirect("despachos_web")


@facultad_requerida("despachar")
def obras_buscar(request):
    # El autocompletado consulta por nombre desde tres caracteres y muestra hasta diez opciones.
    q = request.GET.get("q", "").strip()
    if len(q) < 3:
        return JsonResponse([], safe=False)
    obras = Obra.objects.filter(nombre__icontains=q).order_by("nombre", "id").values("id", "nombre")[:10]
    return JsonResponse(list(obras), safe=False)
