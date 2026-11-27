from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count, F, Q, Sum
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone

from apps.compras.models import FacturaCompra
from apps.cuentas.roles import ACCESO, ADMIN_BODEGA, permisos_de
from apps.despachos.models import Despacho
from .models import Articulo, BitacoraAuditoria, MovimientoCardex, Obra


MODULOS_OPERATIVOS = (
    ("inicio", "Inicio", "fa-gauge-high", "inicio"),
    ("bodega", "Bodega", "fa-warehouse", "bodega_web"),
    ("despacho", "Despachos", "fa-truck-fast", "despachos"),
    ("catalogo", "Catálogo", "fa-boxes-stacked", "catalogo_listar"),
    ("auditoria", "Auditoría", "fa-clipboard-check", "auditoria"),
    ("compras", "Compras", "fa-cart-shopping", "compras"),
    ("finanzas", "Finanzas", "fa-coins", "finanzas"),
    ("administracion", "Empleados", "fa-users", "empleados:empleados"),
    ("reportes", "Reportes", "fa-chart-bar", "reportes:inicio"),
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
        "administracion": permisos["ver_empleados"],
        "reportes": any(permisos[f"reporte_{tipo}"] for tipo in ("ingresos", "egresos", "inventario", "cardex")),
    }
    menu = {
        clave: {"clave": clave, "nombre": nombre, "icono": icono, "url": reverse(ruta)}
        for clave, nombre, icono, ruta in MODULOS_OPERATIVOS if visibles[clave]
    }
    if recepcion and not permisos["despachar"]:
        menu["despacho"]["nombre"] = "Recepción de despachos"
    return {
        "panel_operativo": True,
        "articulos": Articulo.objects.order_by("sku") if permisos["despachar"] else (),
        "rol_actual": rol,
        "perm": permisos,
        "recepcion": recepcion,
        "modulos_operativos": menu,
    }


def indicadores_inicio(contexto):
    permisos = contexto["perm"]
    with timezone.override("America/Santiago"):
        hoy = timezone.localdate()
        inicio_mes = hoy.replace(day=1)
        if permisos["ver_catalogo"]:
            critico = Q(stock_fisico__lte=F("stock_critico"))
            bajo = Q(stock_fisico__gt=F("stock_critico"), stock_fisico__lte=F("stock_minimo"))
            stock = Articulo.objects.aggregate(
                total=Count("pk"), criticos=Count("pk", filter=critico),
                bajos=Count("pk", filter=bajo),
            )
            stock["normales"] = stock["total"] - stock["criticos"] - stock["bajos"]
            contexto["resumen_stock"] = stock
            contexto["distribucion_stock"] = (
                ("Crítico", stock["criticos"], "critico"),
                ("Bajo", stock["bajos"], "bajo"),
                ("Normal", stock["normales"], "normal"),
            )
            contexto["alertas_stock"] = list(
                Articulo.objects.filter(critico | bajo).order_by("stock_fisico", "sku").only(
                    "sku", "descripcion", "stock_fisico", "stock_critico", "stock_minimo", "unidad_medida",
                )[:5],
            )
        if permisos["gestionar_obras"] or permisos["consultar_despachos"]:
            contexto["obras_activas"] = Obra.objects.filter(
                Q(estado__iexact="ACTIVA") | Q(estado__iexact="EN_PROCESO") | Q(estado__iexact="En proceso")
            ).count()
        if permisos["reporte_ingresos"]:
            facturas = FacturaCompra.objects.filter(
                fecha_emision__range=(inicio_mes, hoy),
                tipo_documento=FacturaCompra.TipoDocumento.FACTURA,
            )
            calculos = {"cantidad": Count("pk")}
            if permisos["ver_costos"]:
                calculos["total"] = Sum("total", default=0)
            contexto["compras_mes"] = facturas.aggregate(**calculos)
        if permisos["reporte_cardex"]:
            movimientos = MovimientoCardex.objects.all()
            contexto["actividad_cardex"] = list(
                movimientos.select_related("articulo", "obra").order_by("-fecha_hora", "-pk")[:6],
            )
            contexto["uso_materiales"] = list(
                movimientos.filter(
                    tipo__in=(MovimientoCardex.Tipo.DESPACHO, MovimientoCardex.Tipo.USO_INTERNO),
                    fecha_hora__date__range=(inicio_mes, hoy),
                ).values(unidad=F("articulo__unidad_medida"))
                .annotate(cantidad=Sum("cantidad")).order_by("unidad"),
            )
            contexto["max_uso_materiales"] = max(
                (fila["cantidad"] for fila in contexto["uso_materiales"]), default=1,
            )


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
        indicadores_inicio(contexto)
        if permisos["despachar"]:
            ultimos = Despacho.objects.filter(tipo_movimiento__iexact="DESPACHO")
            if contexto["rol_actual"] != ADMIN_BODEGA and not permisos["consultar_despachos"]:
                ultimos = ultimos.filter(usuario=request.user)
            contexto["ultimos_despachos"] = ultimos.select_related("articulo", "obra").order_by("-fecha_despacho", "-pk")[:5]
    elif modulo == "bodega":
        contexto["existencias"] = Articulo.objects.select_related("categoria").order_by("sku")[:20]
    elif modulo == "auditoria":
        contexto["eventos"] = BitacoraAuditoria.objects.select_related("usuario").order_by("-fecha_hora", "-pk")[:20]
    elif modulo == "compras":
        contexto["facturas"] = FacturaCompra.objects.select_related("proveedor", "documento_origen").order_by(
            "-fecha_emision", "-pk",
        )[:20]
    elif modulo == "finanzas":
        # Las guías registran entradas físicas. Finanzas suma las facturas y resta las notas ya aplicadas.
        facturas = Q(tipo_documento__iexact=FacturaCompra.TipoDocumento.FACTURA)
        creditos = Q(
            tipo_documento=FacturaCompra.TipoDocumento.NOTA_CREDITO,
            estado_ingreso=FacturaCompra.EstadoIngreso.CONFIRMADO, documento_origen__isnull=False,
        )
        contexto["resumen"] = FacturaCompra.objects.aggregate(
            facturas=Count("pk", filter=facturas),
            **{
                nombre: Sum(campo, filter=facturas, default=0) - Sum(campo, filter=creditos, default=0)
                for nombre, campo in (("neto", "subtotal_neto"), ("iva", "iva"), ("total", "total"))
            },
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
