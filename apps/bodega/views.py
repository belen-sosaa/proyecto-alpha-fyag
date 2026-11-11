import json
import uuid
from pathlib import Path

from django.contrib import messages
from django.contrib.auth.views import LoginView
from django.db import transaction
from django.urls import reverse
from django.shortcuts import redirect, render
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError
from django.http import JsonResponse

from .models import Articulo, Despacho, Obra,BitacoraAuditoria
from .decorators import rol_requerido
from .forms import ArticuloForm



class AccesoView(LoginView):
    template_name = "login.html"

    def get_success_url(self):
        siguiente = self.get_redirect_url()
        if siguiente:
            return siguiente

        destinos = {
            "web": "bodega_web",
            "mobile": "bodega_mobile",
        }

        destino = self.request.POST.get("destino", "web")
        return reverse(destinos.get(destino, "bodega_web"))


@login_required
def vista_bodega(request):
    articulos = Articulo.objects.select_related("categoria").all()

    return render(request, "bodega.html", {
        "articulos": articulos,
    })


@rol_requerido(
    "Administrador General",
    "Administrador de Bodega",
)
def articulo_crear(request):
    form = ArticuloForm(
        request.POST if request.method == "POST" else None
    )

    if request.method == "POST" and form.is_valid():
        try:
            # Artículo y auditoría se guardan juntos.
            with transaction.atomic():
                articulo = form.save()

                BitacoraAuditoria.objects.create(
                    usuario=request.user,
                    accion="CREAR",
                    entidad="Articulo",
                    id_registro=articulo.pk,
                    valor_anterior=None,
                    valor_nuevo={
                        "sku": articulo.sku,
                        "codigo_barras": articulo.codigo_barras,
                        "descripcion": articulo.descripcion,
                        "categoria": articulo.categoria_id,
                        "unidad_medida": articulo.unidad_medida,
                        "stock_fisico": articulo.stock_fisico,
                        "stock_minimo": articulo.stock_minimo,
                        "stock_critico": articulo.stock_critico,
                    },
                    detalle="Creación de artículo.",
                )

        except IntegrityError:
            # Se consulta después de salir de la transacción fallida.
            duplicado = False

            sku = form.cleaned_data["sku"]
            codigo = form.cleaned_data.get("codigo_barras")

            if Articulo.objects.filter(sku=sku).exists():
                form.add_error("sku", "Artículo ya registrado")
                duplicado = True

            if codigo and Articulo.objects.filter(
                codigo_barras=codigo
            ).exists():
                form.add_error(
                    "codigo_barras",
                    "Artículo ya registrado",
                )
                duplicado = True

            if not duplicado:
                raise
        else:
            messages.success(request, "Artículo creado correctamente.")
            return redirect("bodega_web")

    return render(request, "articulo_form.html", {"form": form})


@login_required
def vista_mobile(request):
    return render(request, 'Mobile.html', {
        'articulos': Articulo.objects.select_related('categoria').all(),
        'obras': Obra.objects.all(),
    })


@login_required
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

    usuario = request.user

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

@login_required
def obras_buscar(request):
    q = request.GET.get("q", "").strip()

    if len(q) < 3:
        return JsonResponse([], safe=False)

    obras = (
        Obra.objects
        .filter(nombre__icontains=q)
        .order_by("nombre", "id")
        .values("id", "nombre")[:10]
    )

    return JsonResponse(list(obras), safe=False)
