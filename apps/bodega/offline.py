import hashlib
import json
import logging
from uuid import UUID

from django.contrib.staticfiles import finders
from django.core.exceptions import ValidationError
from django.db import DatabaseError, transaction
from django.db.models import OuterRef, Subquery
from django.http import FileResponse, Http404, JsonResponse
from django.middleware.csrf import get_token
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_GET, require_http_methods

from apps.cuentas.roles import permisos_de
from apps.despachos.views import guardar_despacho
from .conteos import datos_conteo, guardar_conteo
from .models import Articulo, MovimientoCardex, Obra, OperacionSincronizada
from .respaldo import guardar_respaldo_sincronizacion


logger = logging.getLogger(__name__)
FACULTADES = {"despacho": "despachar", "conteo": "registrar_conteo"}


def _error(mensaje, estado):
    return JsonResponse({"mensaje": mensaje}, status=estado)


def _catalogo(request, permitidas):
    ultimo = MovimientoCardex.objects.filter(articulo_id=OuterRef("pk")).order_by("-pk").values("pk")[:1]
    articulos = Articulo.objects.annotate(ultimo_movimiento=Subquery(ultimo)).order_by("sku", "pk").values(
        "id", "sku", "descripcion", "stock_fisico", "ultimo_movimiento", "codigo_barras",
    )
    obras = Obra.objects.filter(estado=Obra.Estado.EN_PROCESO).order_by("nombre", "pk").values("id", "nombre")
    return JsonResponse({
        "usuario": str(request.user.pk), "csrf": get_token(request), "permitidas": permitidas,
        "articulos": list(articulos), "obras": list(obras) if "despacho" in permitidas else [],
    })


@never_cache
@csrf_protect
@require_http_methods(["GET", "POST"])
def sincronizar(request):
    if not request.user.is_authenticated:
        return _error("Inicia sesión para sincronizar tus operaciones.", 401)
    permisos = permisos_de(request.user)
    permitidas = [tipo for tipo, facultad in FACULTADES.items() if permisos[facultad]]
    if request.method == "GET":
        if not permitidas:
            return _error("Tu rol no permite operar sin conexión.", 403)
        return _catalogo(request, permitidas)

    try:
        cuerpo = json.loads(request.body)
        clave = UUID(cuerpo["clave"])
        tipo, datos = cuerpo["tipo"], cuerpo["datos"]
        if not isinstance(tipo, str) or not isinstance(datos, dict):
            raise ValueError
        huella = hashlib.sha256(json.dumps([tipo, datos], sort_keys=True, ensure_ascii=True).encode()).hexdigest()
    except (TypeError, AttributeError, ValueError, KeyError, RecursionError):
        return _error("Envía una operación válida con su identificador único.", 400)

    if cuerpo.get("usuario") != str(request.user.pk):
        return _error("La operación pertenece a otra cuenta.", 403)
    if tipo not in FACULTADES:
        return _error("El tipo de operación no es válido.", 400)
    if tipo not in permitidas:
        return _error("Tu rol no tiene permiso para esta operación.", 403)

    try:
        with transaction.atomic():
            operacion, nueva = OperacionSincronizada.objects.get_or_create(
                usuario=request.user, clave=clave, defaults={"huella": huella},
            )
            if not nueva:
                if operacion.huella != huella:
                    return _error("El identificador ya se utilizó para otros datos.", 409)
                return JsonResponse({**operacion.resultado, "repetida": True})
            if tipo == "despacho":
                registro = guardar_despacho(request.user, datos)
            else:
                registro, _ = guardar_conteo(request.user, *datos_conteo(datos, con_base=True))
            operacion.resultado = {"id": registro.pk, "tipo": tipo}
            operacion.save(update_fields=["resultado"])
            documento = {
                "_id": f"{request.user.pk}:{clave}", "operacion_id": str(clave), "tipo": tipo,
                "usuario_id": request.user.pk, "usuario_rut": getattr(getattr(request.user, "perfilusuario", None), "rut", None),
                "articulos_ids": [registro.articulo_id] if tipo == "despacho" else [detalle["articulo_id"] for detalle in datos["detalles"]],
                "obra_id": registro.obra_id if tipo == "despacho" else None,
                "registro_id": registro.pk,
            }
            transaction.on_commit(lambda: guardar_respaldo_sincronizacion(documento), robust=True)
    except ValidationError as error:
        return _error(error.message, 409 if error.code == "conflicto" else 400)
    except (Articulo.DoesNotExist, Obra.DoesNotExist):
        return _error("La obra o el artículo seleccionado ya no está disponible.", 409)
    except DatabaseError:
        logger.exception("Falló la sincronización de %s.", tipo)
        return _error("No se pudo sincronizar la operación. Se puede reintentar.", 503)
    return JsonResponse(operacion.resultado, status=201)


@require_GET
def operacion_offline(request):
    return render(request, "bodega/offline.html")


@require_GET
def service_worker(request):
    ruta = finders.find("js/service-worker.js")
    if not ruta:
        raise Http404
    respuesta = FileResponse(open(ruta, "rb"), content_type="text/javascript; charset=utf-8")
    respuesta["Cache-Control"] = "no-cache"
    return respuesta
