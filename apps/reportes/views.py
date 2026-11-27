from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import render

from apps.bodega.views import contexto_operativo
from apps.cuentas.roles import rol_principal

from .forms import ReporteIngresosForm, ReporteEgresosForm, ReporteInventarioForm
from .services import ReportQueryService
from .utils.export_excel import export_workbook
from .utils.export_pdf import export_pdf


INGRESOS_ROLES = {"Gerente", "Auditor", "Administrativo de Finanzas", "Administrador de Bodega"}
EGRESOS_ROLES = {"Gerente", "Auditor", "Administrador de Bodega", "Bodeguero"}
INVENTARIO_ROLES = EGRESOS_ROLES


def _authorize(request, allowed):
    if not request.user.is_authenticated:
        raise PermissionDenied
    if rol_principal(request.user) not in allowed:
        raise PermissionDenied("Tu rol no tiene permiso para este reporte.")


def _report_context(request, report, form_class, allowed, title):
    _authorize(request, allowed)
    service = ReportQueryService(request)
    result = service.get(report, form_class)
    context = contexto_operativo(request.user)
    context.update(
        reporte=result,
        report_type=report,
        titulo_panel=title,
        ver_costos=service.ver_costos,
    )
    return context, service


@login_required
def reporte_ingresos(request):
    context, _ = _report_context(
        request, "ingresos", ReporteIngresosForm, INGRESOS_ROLES, "Reporte de ingresos"
    )
    return render(request, "reportes/reporte_ingresos.html", context)


@login_required
def reporte_egresos(request):
    context, _ = _report_context(
        request, "egresos", ReporteEgresosForm, EGRESOS_ROLES, "Reporte de egresos"
    )
    return render(request, "reportes/reporte_egresos.html", context)


@login_required
def reporte_inventario(request):
    context, _ = _report_context(
        request, "inventario", ReporteInventarioForm, INVENTARIO_ROLES, "Reporte de inventario"
    )
    return render(request, "reportes/reporte_inventario.html", context)


@login_required
def exportar_ingresos_excel(request):
    _authorize(request, INGRESOS_ROLES)
    service = ReportQueryService(request)
    result = service.get("ingresos", ReporteIngresosForm)
    headers = ["Fecha", "Tipo Doc.", "Folio/Ref.", "Proveedor", "Artículo",
               "Cantidad", "Usuario", "Estado"]
    row_builder = lambda row: [
        row["fecha"], row["tipo"], row["folio"], row["proveedor"], row["articulo"],
        row["cantidad"], row["usuario"], row["estado"],
    ]
    totals = ["", "", "", "", "TOTAL", result["totals"]["cantidad"], "", ""]
    if service.ver_costos:
        headers[5:5] = []
        headers = ["Fecha", "Tipo Doc.", "Folio/Ref.", "Proveedor", "Artículo",
                   "Cantidad", "Costo Unitario", "Total Neto", "IVA", "Total", "Usuario", "Estado"]
        row_builder = lambda row: [
            row["fecha"], row["tipo"], row["folio"], row["proveedor"], row["articulo"],
            row["cantidad"], row["costo"], row["neto"], row["iva"], row["total"],
            row["usuario"], row["estado"],
        ]
        totals = ["", "", "", "", "TOTAL", result["totals"]["cantidad"],
                  "", result["totals"]["neto"], result["totals"]["iva"],
                  result["totals"]["total"], "", ""]
    return export_workbook(
        "Reporte de ingresos", headers,
        [row_builder(row) for row in result["rows"]],
        totals, result["filters"], "reporte-ingresos.xlsx", service.ver_costos,
    )


@login_required
def exportar_ingresos_pdf(request):
    _authorize(request, INGRESOS_ROLES)
    service = ReportQueryService(request)
    result = service.get("ingresos", ReporteIngresosForm)
    headers = ["Fecha", "Tipo", "Folio/Ref.", "Proveedor", "Artículo",
               "Cantidad", "Usuario", "Estado"]
    rows = [[row["fecha"], row["tipo"], row["folio"], row["proveedor"], row["articulo"],
             row["cantidad"], row["usuario"], row["estado"]] for row in result["rows"]]
    totals = ["", "", "", "", "TOTAL", result["totals"]["cantidad"], "", ""]
    if service.ver_costos:
        headers = ["Fecha", "Tipo", "Folio/Ref.", "Proveedor", "Artículo",
                   "Cantidad", "Costo", "Neto", "IVA", "Total", "Usuario", "Estado"]
        rows = [[row["fecha"], row["tipo"], row["folio"], row["proveedor"], row["articulo"],
                 row["cantidad"], row["costo"], row["neto"], row["iva"], row["total"],
                 row["usuario"], row["estado"]] for row in result["rows"]]
        totals = ["", "", "", "", "TOTAL", result["totals"]["cantidad"],
                  "", result["totals"]["neto"], result["totals"]["iva"],
                  result["totals"]["total"], "", ""]
    return export_pdf(
        "Reporte de ingresos", request.user, headers, rows, totals,
        result["filters"], "reporte-ingresos.pdf",
    )


@login_required
def exportar_egresos_excel(request):
    _authorize(request, EGRESOS_ROLES)
    service = ReportQueryService(request)
    result = service.get("egresos", ReporteEgresosForm)
    headers = ["Fecha", "Tipo", "Artículo", "Cantidad", "Obra / Proveedor",
               "Usuario", "Justificación / Ref.", "Saldo"]
    rows = [[row["fecha"], row["tipo_display"], row["articulo"], row["cantidad"],
             row["destino"], row["usuario"],
             row["justificacion"] if not row["referencia"] else f"{row['justificacion']} / {row['referencia']}",
             row["saldo"]] for row in result["rows"]]
    totals = ["", "", "TOTAL GENERAL", result["totals"]["general"], "", "", "", ""]
    return export_workbook(
        "Reporte de egresos", headers, rows, totals, result["filters"],
        "reporte-egresos.xlsx", True,
    )


@login_required
def exportar_egresos_pdf(request):
    _authorize(request, EGRESOS_ROLES)
    service = ReportQueryService(request)
    result = service.get("egresos", ReporteEgresosForm)
    headers = ["Fecha", "Tipo", "Artículo", "Cantidad", "Obra / Proveedor",
               "Usuario", "Justificación / Ref.", "Saldo"]
    rows = [[row["fecha"], row["tipo_display"], row["articulo"], row["cantidad"],
             row["destino"], row["usuario"],
             row["justificacion"] if not row["referencia"] else f"{row['justificacion']} / {row['referencia']}",
             row["saldo"]] for row in result["rows"]]
    totals = ["", "", "TOTAL GENERAL", result["totals"]["general"], "", "", "", ""]
    return export_pdf(
        "Reporte de egresos", request.user, headers, rows, totals,
        result["filters"], "reporte-egresos.pdf",
    )


@login_required
def exportar_inventario_excel(request):
    _authorize(request, INVENTARIO_ROLES)
    service = ReportQueryService(request)
    result = service.get("inventario", ReporteInventarioForm)
    headers = ["SKU", "Artículo", "Categoría", "Ubicación", "Stock Actual",
               "Stock Mínimo", "Stock Crítico", "Estado"]
    rows = [[row["sku"], row["articulo"], row["categoria"], row["ubicacion"],
             row["stock"], row["stock_minimo"], row["stock_critico"], row["estado_display"]]
            for row in result["rows"]]
    totals = ["", "", "", "TOTAL", result["totals"]["unidades"], "", "", ""]
    if service.ver_costos:
        headers += ["Precio Lista", "Valorización"]
        rows = [[row["sku"], row["articulo"], row["categoria"], row["ubicacion"],
                 row["stock"], row["stock_minimo"], row["stock_critico"], row["estado_display"],
                 row["precio"], row["valorizacion"]] for row in result["rows"]]
        totals = ["", "", "", "TOTAL", result["totals"]["unidades"], "", "",
                  "", "", result["totals"]["valorizacion"]]
    return export_workbook(
        "Reporte de inventario", headers, rows, totals, result["filters"],
        "reporte-inventario.xlsx", service.ver_costos,
    )


@login_required
def exportar_inventario_pdf(request):
    _authorize(request, INVENTARIO_ROLES)
    service = ReportQueryService(request)
    result = service.get("inventario", ReporteInventarioForm)
    headers = ["SKU", "Artículo", "Categoría", "Ubicación", "Stock", "Mínimo", "Crítico", "Estado"]
    rows = [[row["sku"], row["articulo"], row["categoria"], row["ubicacion"],
             row["stock"], row["stock_minimo"], row["stock_critico"], row["estado_display"]]
            for row in result["rows"]]
    totals = ["", "", "", "TOTAL", result["totals"]["unidades"], "", "", ""]
    if service.ver_costos:
        headers += ["Precio Lista", "Valorización"]
        rows = [[row["sku"], row["articulo"], row["categoria"], row["ubicacion"],
                 row["stock"], row["stock_minimo"], row["stock_critico"], row["estado_display"],
                 row["precio"], row["valorizacion"]] for row in result["rows"]]
        totals = ["", "", "", "TOTAL", result["totals"]["unidades"], "", "",
                  "", "", result["totals"]["valorizacion"]]
    return export_pdf(
        "Reporte de inventario", request.user, headers, rows, totals,
        result["filters"], "reporte-inventario.pdf",
    )
