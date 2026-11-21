from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Sum
from django.shortcuts import render
from django.urls import reverse

from apps.compras.models import FacturaCompra
from apps.cuentas.roles import ACCESO, SUPERVISOR, permisos_de
from .models import Articulo, BitacoraAuditoria


MODULOS_OPERATIVOS = (
    ("bodega", "Bodega", "fa-warehouse", "bodega_web"),
    ("despacho", "Despacho", "fa-truck-fast", "despachos"),
    ("catalogo", "Catálogo", "fa-boxes-stacked", "catalogo_listar"),
    ("auditoria", "Auditoría", "fa-clipboard-check", "auditoria"),
    ("compras", "Compras", "fa-cart-shopping", "compras"),
    ("finanzas", "Finanzas", "fa-coins", "finanzas"),
)
MODULOS_DEL_PANEL = {"bodega", "auditoria", "compras", "finanzas"}


def contexto_operativo(usuario):
    permisos = permisos_de(usuario)
    rol = permisos["rol"]
    return {
        "articulos": Articulo.objects.order_by("sku") if permisos["despachar"] else (),
        "rol_actual": rol,
        "perm": permisos,
        "recepcion": rol == SUPERVISOR,
    }


@login_required
def vista_bodega(request, modulo=None):
    contexto = contexto_operativo(request.user)
    permisos = contexto["perm"]
    accesos = ACCESO.get(contexto["rol_actual"], ())
    visibles = {
        "bodega": permisos["despachar"] or permisos["registrar_merma"],
        "despacho": permisos["despachar"] or contexto["recepcion"],
        "catalogo": permisos["ver_catalogo"],
        "auditoria": "cardex" in accesos,
        "compras": "compras" in accesos,
        "finanzas": permisos["ver_costos"],
    }
    menu = {
        clave: {"clave": clave, "nombre": nombre, "icono": icono, "url": reverse(ruta)}
        for clave, nombre, icono, ruta in MODULOS_OPERATIVOS if visibles[clave]
    }
    if modulo is not None and modulo not in menu:
        raise PermissionDenied
    if modulo is None:
        modulo = next((clave for clave in menu if clave in MODULOS_DEL_PANEL), None)

    contexto.update(modulos_operativos=menu.values(), modulo_actual=menu.get(modulo))
    if modulo == "bodega":
        contexto["existencias"] = Articulo.objects.select_related("categoria").order_by("sku")[:20]
    elif modulo == "auditoria":
        contexto["eventos"] = BitacoraAuditoria.objects.select_related("usuario").order_by("-fecha_hora", "-pk")[:20]
    elif modulo == "compras":
        contexto["facturas"] = FacturaCompra.objects.order_by("-fecha_emision", "-pk").values(
            "folio", "fecha_emision", "proveedor__razon_social",
        )[:20]
    elif modulo == "finanzas":
        contexto["resumen"] = FacturaCompra.objects.aggregate(
            facturas=Count("pk"), neto=Sum("subtotal_neto", default=0),
            iva=Sum("iva", default=0), total=Sum("total", default=0),
        )
    return render(request, "bodega/bodega.html", contexto)
