from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from apps.cuentas.roles import ACCESO, SUPERVISOR, permisos_de
from .models import Articulo


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
        "articulos": Articulo.objects.select_related("categoria").all()
        if mostrar_catalogo or permisos["despachar"] else (),
        "rol_actual": rol,
        "perm": permisos,
        "recepcion": recepcion,
        "mostrar_bodega": mostrar_bodega,
        "mostrar_catalogo": mostrar_catalogo,
        "mostrar_finanzas": mostrar_finanzas,
    }


@login_required
def vista_bodega(request):
    return render(request, "bodega/bodega.html", contexto_operativo(request.user))
