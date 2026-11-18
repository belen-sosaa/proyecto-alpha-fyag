import json
import uuid
from pathlib import Path

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import redirect, render

from apps.bodega.models import Articulo, Despacho, Obra
from apps.bodega.views import contexto_operativo
from apps.cuentas.decorators import facultad_requerida


@login_required
def vista_mobile(request):
    return render(request, "despachos/mobile.html", contexto_operativo(request.user))


@facultad_requerida("despachar")
@transaction.atomic
def procesar_despacho(request):
    if request.method != "POST":
        return redirect("bodega_mobile")
    obra_id = request.POST.get("obra")
    articulo_id = request.POST.get("articulo")
    if not obra_id or not articulo_id:
        messages.error(request, "Debe seleccionar una obra y un artículo.")
        return redirect("bodega_mobile")
    try:
        cantidad = int(request.POST.get("cantidad", "0"))
    except (TypeError, ValueError):
        messages.error(request, "La cantidad ingresada no es válida.")
        return redirect("bodega_mobile")
    if cantidad <= 0:
        messages.error(request, "La cantidad debe ser mayor que cero.")
        return redirect("bodega_mobile")
    try:
        articulo = Articulo.objects.select_for_update().select_related("categoria").get(pk=int(articulo_id))
        obra = Obra.objects.get(pk=int(obra_id))
    except (Articulo.DoesNotExist, Obra.DoesNotExist, TypeError, ValueError):
        messages.error(request, "La obra o el artículo seleccionado no existe.")
        return redirect("bodega_mobile")
    if articulo.stock_fisico < cantidad:
        messages.error(request, "Stock insuficiente para realizar el despacho.")
        return redirect("bodega_mobile")

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
        return redirect("bodega_mobile")
    articulo.stock_fisico -= cantidad
    articulo.save(update_fields=["stock_fisico"])
    despacho.save()
    messages.success(request, "Despacho registrado correctamente.")
    return redirect("bodega_mobile")


@facultad_requerida("despachar")
def obras_buscar(request):
    q = request.GET.get("q", "").strip()
    if len(q) < 3:
        return JsonResponse([], safe=False)
    obras = Obra.objects.filter(nombre__icontains=q).order_by("nombre", "id").values("id", "nombre")[:10]
    return JsonResponse(list(obras), safe=False)
