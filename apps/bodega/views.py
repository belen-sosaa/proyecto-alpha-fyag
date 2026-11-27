from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count, F, Q, Sum
from django.shortcuts import render
from django.urls import reverse

from apps.compras.models import FacturaCompra
from apps.cuentas.roles import ACCESO, ADMIN_BODEGA, permisos_de
from apps.despachos.models import Despacho
from .models import Articulo, BitacoraAuditoria


MODULOS_OPERATIVOS = (
    ("inicio", "Inicio", "fa-gauge-high", "inicio"),
    ("bodega", "Bodega", "fa-warehouse", "bodega_web"),
    ("despacho", "Despachos", "fa-truck-fast", "despachos"),
    ("catalogo", "Catálogo", "fa-boxes-stacked", "catalogo_listar"),
    ("auditoria", "Auditoría", "fa-clipboard-check", "auditoria"),
    ("compras", "Compras", "fa-cart-shopping", "compras"),
    ("finanzas", "Finanzas", "fa-coins", "finanzas"),
)
MODULOS_DEL_PANEL = {"bodega", "auditoria", "compras", "finanzas"}


def contexto_operativo(usuario):
    permisos = permisos_de(usuario)
    rol = permisos["rol"]
    accesos = ACCESO.get(rol, ())
    recepcion = permisos["firmar_recepcion"]
    visibles = {
        "inicio": True,
        "bodega": permisos["despachar"] or permisos["registrar_merma"],
        "despacho": permisos["despachar"] or permisos["consultar_despachos"] or recepcion,
        "catalogo": permisos["ver_catalogo"],
        "auditoria": "cardex" in accesos,
        "compras": "compras" in accesos,
        "finanzas": permisos["ver_costos"],
    }
    menu = {
        clave: {"clave": clave, "nombre": nombre, "icono": icono, "url": reverse(ruta)}
        for clave, nombre, icono, ruta in MODULOS_OPERATIVOS if visibles[clave]
    }
    if recepcion:
        menu["despacho"]["nombre"] = "Recepción de despachos"
    return {
        "panel_operativo": True,
        "articulos": Articulo.objects.order_by("sku") if permisos["despachar"] else (),
        "rol_actual": rol,
        "perm": permisos,
        "recepcion": recepcion,
        "modulos_operativos": menu,
    }


@login_required
def vista_bodega(request, modulo=None):
    contexto = contexto_operativo(request.user)
    permisos = contexto["perm"]
    menu = contexto["modulos_operativos"]
    if modulo is not None and modulo not in menu:
        raise PermissionDenied
    if modulo is None:
        modulo = next((clave for clave in menu if clave in MODULOS_DEL_PANEL), None)

    modulo_actual = menu.get(modulo, {})
    contexto.update(
        modulo_actual=modulo_actual,
        titulo_panel=modulo_actual.get("nombre", "Panel operativo"),
    )
    if modulo == "inicio":
        if permisos["ver_catalogo"]:
            contexto["resumen_stock"] = Articulo.objects.aggregate(
                total=Count("pk"), criticos=Count("pk", filter=Q(stock_fisico__lte=F("stock_critico"))),
            )
        if permisos["despachar"]:
            ultimos = Despacho.objects.filter(tipo_movimiento__iexact="DESPACHO")
            if contexto["rol_actual"] != ADMIN_BODEGA:
                ultimos = ultimos.filter(usuario=request.user)
            contexto["ultimos_despachos"] = ultimos.select_related("articulo", "obra").order_by("-fecha_despacho", "-pk")[:5]
    elif modulo == "bodega":
        contexto["existencias"] = Articulo.objects.select_related("categoria").order_by("sku")[:20]
    elif modulo == "auditoria":
        contexto["eventos"] = BitacoraAuditoria.objects.select_related("usuario").order_by("-fecha_hora", "-pk")[:20]
    elif modulo == "compras":
        contexto["facturas"] = FacturaCompra.objects.order_by("-fecha_emision", "-pk").values(
            "pk", "folio", "fecha_emision", "proveedor__razon_social", "estado_ingreso",
        )[:20]
    elif modulo == "finanzas":
        contexto["resumen"] = FacturaCompra.objects.aggregate(
            facturas=Count("pk"), neto=Sum("subtotal_neto", default=0),
            iva=Sum("iva", default=0), total=Sum("total", default=0),
        )
    return render(request, "bodega/bodega.html", contexto)


@login_required
def vista_cardex(request):
    contexto = contexto_operativo(request.user)
    auditoria = contexto["modulos_operativos"].get("auditoria")
    if auditoria is None:
        raise PermissionDenied
    movimientos = BitacoraAuditoria.objects.select_related("usuario").order_by("-fecha_hora", "-pk")
    contexto.update(
        modulo_actual=auditoria,
        titulo_panel="Cardex de movimientos",
        pagina=Paginator(movimientos, 25).get_page(request.GET.get("pagina")),
        entidades_de_compra={"FacturaCompra", "DetalleFactura", "DetalleFacturaCompra"},
    )
    return render(request, "bodega/cardex.html", contexto)
