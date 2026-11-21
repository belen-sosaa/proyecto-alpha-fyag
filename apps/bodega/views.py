from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Count, F, Q
from django.shortcuts import redirect, render

from apps.cuentas.roles import ACCESO, SUPERVISOR, permisos_de
from .models import Articulo, Despacho


def contexto_operativo(usuario):
    # Inicio y los módulos comparten estos datos para mostrar el menú y el perfil según el rol.
    permisos = permisos_de(usuario)
    rol = permisos["rol"]
    modulos = ACCESO.get(rol, ())
    recepcion = rol == SUPERVISOR
    # Cada enlace refleja una función distinta. El Supervisor entra a Despachos para recibir.
    mostrar_bodega = permisos["registrar_merma"] or permisos["ingresar_articulo"]
    mostrar_despachos = permisos["despachar"] or recepcion
    mostrar_catalogo = permisos["ver_catalogo"]
    mostrar_auditoria = "cardex" in modulos
    mostrar_finanzas = "compras" in modulos and permisos["ver_costos"]
    return {
        "panel_operativo": True,
        "rol_actual": rol,
        "perm": permisos,
        "recepcion": recepcion,
        "mostrar_bodega": mostrar_bodega,
        "mostrar_despachos": mostrar_despachos,
        "mostrar_catalogo": mostrar_catalogo,
        "mostrar_auditoria": mostrar_auditoria,
        "mostrar_finanzas": mostrar_finanzas,
    }


@login_required
def vista_inicio(request):
    contexto = contexto_operativo(request.user)
    contexto["titulo_panel"] = "Inicio"
    if contexto["mostrar_catalogo"]:
        # El conteo compara cada existencia con su propio umbral directamente en la base de datos.
        contexto["resumen_stock"] = Articulo.objects.aggregate(
            total=Count("pk"),
            criticos=Count("pk", filter=Q(stock_fisico__lte=F("stock_critico"))),
        )
    if contexto["perm"]["despachar"]:
        # El ID desempata despachos con la misma fecha y mantiene un orden estable.
        contexto["despachos_recientes"] = Despacho.objects.select_related(
            "articulo", "obra"
        ).order_by("-fecha_despacho", "-pk")[:5]
    return render(request, "cuentas/inicio.html", contexto)


@login_required
def vista_bodega(request):
    contexto = contexto_operativo(request.user)
    if not contexto["mostrar_bodega"]:
        if contexto["mostrar_despachos"]:
            return redirect("despachos_web")
        return redirect("inventario" if contexto["mostrar_catalogo"] else "inicio")
    contexto["titulo_panel"] = "Bodega"
    return render(request, "bodega/bodega.html", contexto)


@login_required
def vista_inventario(request):
    contexto = contexto_operativo(request.user)
    if not contexto["mostrar_catalogo"]:
        raise PermissionDenied
    contexto["titulo_panel"] = "Catálogo"
    # Se trae la categoría junto con cada artículo para evitar consultarla fila por fila.
    contexto["articulos"] = Articulo.objects.select_related("categoria").order_by("sku")
    return render(request, "bodega/inventario.html", contexto)


@login_required
def vista_auditoria(request):
    # El módulo se separa del catálogo y queda reservado a quienes tienen acceso a Cardex.
    contexto = contexto_operativo(request.user)
    if not contexto["mostrar_auditoria"]:
        raise PermissionDenied
    contexto["titulo_panel"] = "Auditoría"
    return render(request, "bodega/auditoria.html", contexto)


@login_required
def vista_compras(request):
    # La vista actual muestra la maqueta de compras y aplica el permiso de costos antes de renderizarla.
    contexto = contexto_operativo(request.user)
    if not contexto["mostrar_finanzas"]:
        raise PermissionDenied
    contexto["titulo_panel"] = "Compras"
    return render(request, "bodega/compras.html", contexto)
