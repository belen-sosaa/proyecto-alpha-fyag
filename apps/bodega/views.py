from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Count, F, Q
from django.shortcuts import redirect, render

from apps.cuentas.roles import ACCESO, SUPERVISOR, permisos_de
from .models import Articulo, Despacho


def contexto_operativo(usuario):
    permisos = permisos_de(usuario)
    rol = permisos["rol"]
    modulos = ACCESO.get(rol, ())
    recepcion = rol == SUPERVISOR
    mostrar_bodega = permisos["despachar"] or permisos["registrar_merma"] or recepcion
    mostrar_catalogo = "catalogo" in modulos or "cardex" in modulos
    mostrar_finanzas = "compras" in modulos and permisos["ver_costos"]
    return {
        "panel_operativo": True,
        "rol_actual": rol,
        "perm": permisos,
        "recepcion": recepcion,
        "mostrar_bodega": mostrar_bodega,
        "mostrar_catalogo": mostrar_catalogo,
        "mostrar_finanzas": mostrar_finanzas,
    }


@login_required
def vista_inicio(request):
    contexto = contexto_operativo(request.user)
    contexto["titulo_panel"] = "Inicio"
    if contexto["mostrar_catalogo"]:
        contexto["resumen_stock"] = Articulo.objects.aggregate(
            total=Count("pk"),
            criticos=Count("pk", filter=Q(stock_fisico__lte=F("stock_critico"))),
        )
    if contexto["perm"]["despachar"]:
        contexto["despachos_recientes"] = Despacho.objects.select_related(
            "articulo", "obra"
        ).order_by("-fecha_despacho", "-pk")[:5]
    return render(request, "cuentas/inicio.html", contexto)


@login_required
def vista_bodega(request):
    contexto = contexto_operativo(request.user)
    if not contexto["mostrar_bodega"]:
        return redirect("inventario" if contexto["mostrar_catalogo"] else "inicio")
    contexto["titulo_panel"] = (
        "Recepción de despachos" if contexto["recepcion"] else "Bodega y Despachos"
    )
    if contexto["perm"]["despachar"]:
        contexto["articulos"] = Articulo.objects.order_by("sku")
    return render(request, "bodega/bodega.html", contexto)


@login_required
def vista_inventario(request):
    contexto = contexto_operativo(request.user)
    if not contexto["mostrar_catalogo"]:
        raise PermissionDenied
    contexto["titulo_panel"] = "Catálogo y Auditoría"
    contexto["articulos"] = Articulo.objects.select_related("categoria").order_by("sku")
    return render(request, "bodega/inventario.html", contexto)


@login_required
def vista_compras(request):
    contexto = contexto_operativo(request.user)
    if not contexto["mostrar_finanzas"]:
        raise PermissionDenied
    contexto["titulo_panel"] = "Compras y Finanzas"
    return render(request, "bodega/compras.html", contexto)
