import json
import uuid
from pathlib import Path

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.auth.views import LoginView
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.db.models.deletion import ProtectedError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from .decorators import rol_requerido
from .forms import ArticuloForm, ArticuloCatalogoForm
from .models import (
    Articulo,
    BitacoraAuditoria,
    Categoria,
    Despacho,
    Obra,
)



# AUTENTICACIÓN Y VISTAS GENERALES DE BODEGA

class AccesoView(LoginView):
    """Vista de inicio de sesión que redirige según el dispositivo (Web o Mobile)."""
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
    """Panel principal de bodega (Escritorio)."""
    articulos = Articulo.objects.select_related("categoria").all()

    return render(request, "bodega.html", {
        "articulos": articulos,
    })


@rol_requerido(
    "Administrador General",
    "Administrador de Bodega",
)
def articulo_crear(request):
    """Creación rápida de artículos desde el panel de bodega con control RBAC por rol."""
    form = ArticuloForm(
        request.POST if request.method == "POST" else None
    )

    if request.method == "POST" and form.is_valid():
        try:
            # Transacción ACID: el artículo y su registro de auditoría se guardan juntos.
            with transaction.atomic():
                articulo = form.save()

                BitacoraAuditoria.objects.create(
                    usuario=request.user,
                    accion="CREAR",
                    entidad="Articulo",
                    id_registro=articulo.pk,
                    valor_anterior=None,
                    valor_nuevo=datos_articulo(articulo),
                    detalle="Creación de artículo.",
                )

        except IntegrityError:
            # Validación de duplicados (SKU o código de barras) fuera de la transacción fallida.
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



# OPERACIÓN MÓVIL (TABLET) Y DESPACHOS A OBRA


@login_required
def vista_mobile(request):
    """Interfaz Mobile-First para tablets en pasillos de bodega."""
    return render(request, "Mobile.html", {
        "articulos": Articulo.objects.select_related("categoria").all(),
        "obras": Obra.objects.all(),
    })


@login_required
@transaction.atomic
def procesar_despacho(request):
    """Procesa la salida de materiales a obra validando stock con bloqueo de fila (ACID)."""
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
        # select_for_update() bloquea la fila en PostgreSQL para evitar concurrencia.
        articulo = (
            Articulo.objects
            .select_for_update()
            .select_related("categoria")
            .get(pk=int(articulo_id))
        )
        obra = Obra.objects.get(pk=int(obra_id))
    except (Articulo.DoesNotExist, Obra.DoesNotExist, TypeError, ValueError):
        messages.error(request, "La obra o el artículo seleccionado no existe.")
        return redirect("bodega_mobile")

    if articulo.stock_fisico < cantidad:
        messages.error(request, "Stock insuficiente para realizar el despacho.")
        return redirect("bodega_mobile")

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
        "_id": mongo_id,
        "operacion": "despacho_material",
        "dispositivo": request.POST.get("dispositivo", "tablet_operativa"),
        "geo_ubicacion": {
            "latitud": obra.latitud,
            "longitud": obra.longitud,
        },
        "tag_iot": request.POST.get("tag_iot", ""),
        "sku": articulo.sku,
        "descripcion": articulo.descripcion,
        "obra": obra.nombre,
        "cantidad": cantidad,
    }

    mock_file = Path(__file__).resolve().parent.parent / "mock_mongodb_cardex.json"
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


@login_required
def obras_buscar(request):
    """Autocompletado de obras al escribir al menos 3 caracteres (HU-04)."""
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



# CRUD DE INSUMOS (CATÁLOGO) CON RBAC, FILTROS, AUDITORÍA Y BLOQUEO DE BORRADO


def datos_articulo(articulo):
    """Función auxiliar: serializa los datos del insumo para la Bitácora de Auditoría."""
    return {
        "sku": articulo.sku,
        "codigo_barras": articulo.codigo_barras,
        "descripcion": articulo.descripcion,
        "categoria": articulo.categoria_id,
        "unidad_medida": articulo.unidad_medida,
        "ubicacion_fisica": articulo.ubicacion_fisica,
        "stock_fisico": articulo.stock_fisico,
        "stock_minimo": articulo.stock_minimo,
        "stock_critico": articulo.stock_critico,
    }



# 1. READ (Leer / Listar del CRUD) + Búsqueda y Filtros Combinados + RBAC
@login_required
@permission_required("bodega.view_articulo", raise_exception=True)
@require_http_methods(["GET"])
def catalogo_listar(request):
    """
    Lista todos los insumos del catálogo permitiendo:
    - Buscar por texto (SKU, descripción, código de barras o ubicación física).
    - Filtrar por categoría técnica.
    - Controlar acceso de lectura mediante permiso RBAC (view_articulo).
    """
    busqueda = request.GET.get("q", "").strip()
    categoria_id = request.GET.get("categoria", "").strip()

    articulos = Articulo.objects.select_related("categoria").order_by("sku")

    # Filtro de búsqueda por múltiples criterios (SKU, descripción, barra o ubicación)
    if busqueda:
        articulos = articulos.filter(
            Q(sku__icontains=busqueda)
            | Q(descripcion__icontains=busqueda)
            | Q(codigo_barras__icontains=busqueda)
            | Q(ubicacion_fisica__icontains=busqueda)
        )

    # Filtro desplegable por categoría
    if categoria_id:
        if categoria_id.isdecimal():
            articulos = articulos.filter(categoria_id=int(categoria_id))
        else:
            articulos = articulos.none()

    return render(request, "catalogo.html", {
        "articulos": articulos,
        "categorias": Categoria.objects.order_by("nombre"),
        "q": busqueda,
        "categoria_seleccionada": categoria_id,
    })



# 2. CREATE (Crear del CRUD) + Prevención de Duplicados + Auditoría + RBAC
@login_required
@permission_required("bodega.add_articulo", raise_exception=True)
@require_http_methods(["GET", "POST"])
def catalogo_crear(request):
    """
    Crea un nuevo insumo en el catálogo:
    - Exige permiso RBAC de creación (add_articulo).
    - Valida que no se duplique el SKU ni el código de barras (HU-02).
    - Registra la huella digital en BitacoraAuditoria (acción CREAR).
    """
    form = ArticuloCatalogoForm(
        request.POST if request.method == "POST" else None
    )

    if request.method == "POST" and form.is_valid():
        try:
            # Transacción atómica: guarda el insumo y la auditoría en conjunto
            with transaction.atomic():
                articulo = form.save()

                BitacoraAuditoria.objects.create(
                    usuario=request.user,
                    accion="CREAR",
                    entidad="Articulo",
                    id_registro=articulo.pk,
                    valor_anterior=None,
                    valor_nuevo=datos_articulo(articulo),
                    detalle="Creación desde el catálogo.",
                )
        except IntegrityError:
            form.add_error(
                None,
                "No se pudo guardar. Verifica que el SKU y el "
                "código de barras no estén registrados.",
            )
        else:
            messages.success(request, "Insumo creado correctamente.")
            return redirect("catalogo_listar")

    return render(request, "catalogo.html", {
        "form": form,
        "titulo": "Crear insumo",
    })



# 3. UPDATE (Actualizar / Editar del CRUD) + Bloqueo de Concurrencia + RBAC
@login_required
@permission_required("bodega.change_articulo", raise_exception=True)
@require_http_methods(["GET", "POST"])
def catalogo_editar(request, pk):
    """
    Edita los datos de un insumo existente:
    - Exige permiso RBAC de edición (change_articulo).
    - Aplica bloqueo a nivel de fila (select_for_update) para evitar concurrencia.
    - Guarda en BitacoraAuditoria el valor anterior y el valor nuevo (acción EDITAR).
    """
    if request.method == "POST":
        try:
            with transaction.atomic():
                # Bloquea el registro mientras se actualiza
                articulo = get_object_or_404(
                    Articulo.objects.select_for_update(),
                    pk=pk,
                )

                # Captura el estado previo para la auditoría
                anterior = datos_articulo(articulo)
                form = ArticuloCatalogoForm(
                    request.POST,
                    instance=articulo,
                )

                if form.is_valid():
                    articulo = form.save()

                    BitacoraAuditoria.objects.create(
                        usuario=request.user,
                        accion="EDITAR",
                        entidad="Articulo",
                        id_registro=articulo.pk,
                        valor_anterior=anterior,
                        valor_nuevo=datos_articulo(articulo),
                        detalle="Actualización desde el catálogo.",
                    )

                    messages.success(
                        request,
                        "Insumo actualizado correctamente.",
                    )
                    return redirect("catalogo_listar")

        except IntegrityError:
            form.add_error(
                None,
                "No se pudo guardar. Verifica que el SKU y el "
                "código de barras no estén registrados.",
            )
    else:
        articulo = get_object_or_404(Articulo, pk=pk)
        form = ArticuloCatalogoForm(instance=articulo)

    return render(request, "catalogo.html", {
        "form": form,
        "titulo": "Editar insumo",
    })



# 4. DELETE (Eliminar del CRUD) + Bloqueo por Movimientos/Stock + RBAC
@login_required
@permission_required("bodega.delete_articulo", raise_exception=True)
@require_http_methods(["GET", "POST"])
def catalogo_eliminar(request, pk):
    """
    Elimina un insumo únicamente si no compromete la trazabilidad histórica:
    - Exige permiso RBAC de eliminación (delete_articulo).
    - Bloquea el borrado si el insumo registra despachos/movimientos asociados (RF-01).
    - Bloquea el borrado si el insumo aún tiene stock físico mayor a cero.
    - Registra la eliminación y los datos previos en BitacoraAuditoria (acción ELIMINAR).
    """
    if request.method == "POST":
        try:
            with transaction.atomic():
                articulo = get_object_or_404(
                    Articulo.objects.select_for_update(),
                    pk=pk,
                )

                # Candado 1: Bloqueo si tiene movimientos de despacho registrados
                if Despacho.objects.filter(articulo=articulo).exists():
                    messages.error(
                        request,
                        "No se puede eliminar: el insumo tiene despachos.",
                    )
                    return redirect("catalogo_listar")

                # Candado 2: Bloqueo si posee existencias físicas en bodega
                if articulo.stock_fisico > 0:
                    messages.error(
                        request,
                        "No se puede eliminar un insumo con existencias.",
                    )
                    return redirect("catalogo_listar")

                articulo_id = articulo.pk
                anterior = datos_articulo(articulo)

                articulo.delete()

                # Registro en bitácora inalterable tras eliminar
                BitacoraAuditoria.objects.create(
                    usuario=request.user,
                    accion="ELIMINAR",
                    entidad="Articulo",
                    id_registro=articulo_id,
                    valor_anterior=anterior,
                    valor_nuevo=None,
                    detalle="Eliminación de insumo sin movimientos.",
                )

        # Candado 3: Protección de integridad referencial a nivel de base de datos (PROTECT)
        except ProtectedError:
            messages.error(
                request,
                "No se puede eliminar: tiene registros asociados.",
            )
        else:
            messages.success(request, "Insumo eliminado correctamente.")

        return redirect("catalogo_listar")

    articulo = get_object_or_404(Articulo, pk=pk)

    return render(request, "catalogo.html", {
        "articulo": articulo,
    })