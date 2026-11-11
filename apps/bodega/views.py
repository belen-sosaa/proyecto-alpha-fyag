import json
import uuid
from pathlib import Path

from django.contrib import messages
from django.contrib.auth.models import User
from django.db import transaction
from django.shortcuts import redirect, render

from .models import Articulo, Despacho, Obra


def vista_login(request):
    return render(request, 'login.html')


def vista_bodega(request):
    return render(request, 'bodega.html')


def vista_mobile(request):
    return render(request, 'Mobile.html', {
        'articulos': Articulo.objects.select_related('categoria').all(),
        'obras': Obra.objects.all(),
    })


@transaction.atomic
def procesar_despacho(request):
    if request.method != 'POST':
        return redirect('bodega_mobile')

    obra_id = request.POST.get('obra')
    articulo_id = request.POST.get('articulo')

    if not obra_id or not articulo_id:
        messages.error(request, 'Debe seleccionar una obra y un artículo.')
        return redirect('bodega_mobile')

    try:
        cantidad = int(request.POST.get('cantidad', '0'))
    except (TypeError, ValueError):
        messages.error(request, 'La cantidad ingresada no es válida.')
        return redirect('bodega_mobile')

    if cantidad <= 0:
        messages.error(request, 'La cantidad debe ser mayor que cero.')
        return redirect('bodega_mobile')

    try:
        articulo = Articulo.objects.select_for_update().select_related('categoria').get(pk=int(articulo_id))
        obra = Obra.objects.get(pk=int(obra_id))
    except (Articulo.DoesNotExist, Obra.DoesNotExist, TypeError, ValueError):
        messages.error(request, 'La obra o el artículo seleccionado no existe.')
        return redirect('bodega_mobile')

    if articulo.stock_fisico < cantidad:
        messages.error(request, 'Stock insuficiente para realizar el despacho.')
        return redirect('bodega_mobile')

    usuario = request.user if request.user.is_authenticated else User.objects.first()
    if usuario is None:
        messages.error(request, 'Debe existir un usuario registrado para despachar.')
        return redirect('login')

    despacho = Despacho(
        articulo=articulo,
        obra=obra,
        cantidad=cantidad,
        usuario=usuario,
    )

    mongo_id = str(uuid.uuid4())
    despacho.mongodb_id = mongo_id
    mongo_document = {
        '_id': mongo_id,
        'operacion': 'despacho_material',
        'dispositivo': request.POST.get('dispositivo', 'tablet_operativa'),
        'geo_ubicacion': {
            'latitud': obra.latitud,
            'longitud': obra.longitud,
        },
        'tag_iot': request.POST.get('tag_iot', ''),
        'sku': articulo.sku,
        'descripcion': articulo.descripcion,
        'obra': obra.nombre,
        'cantidad': cantidad,
    }

    mock_file = Path(__file__).resolve().parent.parent / 'mock_mongodb_cardex.json'
    try:
        if mock_file.exists():
            with mock_file.open('r', encoding='utf-8') as archivo:
                try:
                    documentos = json.load(archivo)
                except json.JSONDecodeError:
                    documentos = []
                if not isinstance(documentos, list):
                    documentos = []
        else:
            documentos = []

        documentos.append(mongo_document)
        with mock_file.open('w', encoding='utf-8') as archivo:
            json.dump(documentos, archivo, ensure_ascii=False, indent=2)
    except OSError:
        messages.error(request, 'No se pudo registrar el despacho en el almacenamiento local.')
        return redirect('bodega_mobile')

    articulo.stock_fisico -= cantidad
    articulo.save(update_fields=['stock_fisico'])
    despacho.save()

    messages.success(request, 'Despacho registrado correctamente.')
    return redirect('bodega_mobile')
