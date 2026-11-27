from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.http import HttpResponseBadRequest
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET

from apps.bodega.views import contexto_operativo
from apps.cuentas.decorators import facultad_requerida
from apps.cuentas.roles import puede

from .forms import ReporteCardexForm, ReporteIngresosForm, ReporteEgresosForm, ReporteInventarioForm
from .services import CARDEX_COLUMNAS, ReportQueryService, filas_cardex, movimientos_cardex
from .utils.export_excel import export_workbook
from .utils.export_pdf import export_pdf


TIPOS_DE_REPORTE = (
    ("ingresos", "Ingresos", "fa-arrow-down", "Recepciones de materiales, documentos de compra y proveedores."),
    ("egresos", "Egresos", "fa-arrow-up", "Salidas de bodega por despacho, merma o devolución."),
    ("inventario", "Inventario", "fa-boxes-stacked", "Existencias, niveles de stock y artículos por categoría."),
    ("cardex", "Cardex", "fa-clipboard-list", "Historial de movimientos y saldo de cada artículo."),
)


def _authorize(request, report):
    if not puede(request.user, f"reporte_{report}"):
        raise PermissionDenied("Tu rol no tiene permiso para este reporte.")


def _navigation_context(request):
    context = contexto_operativo(request.user)
    modulo = context["modulos_operativos"].get("reportes")
    if modulo is None:
        raise PermissionDenied("Tu rol no tiene permiso para consultar reportes.")
    context.update(
        modulo_actual=modulo,
        titulo_panel="Reportes",
        opciones_reportes=[
            (tipo, etiqueta, f"reportes:{tipo}", icono, descripcion)
            for tipo, etiqueta, icono, descripcion in TIPOS_DE_REPORTE
            if context["perm"][f"reporte_{tipo}"]
        ],
    )
    return context


@login_required
def inicio(request):
    return render(request, "reportes/inicio.html", _navigation_context(request))


@facultad_requerida("reporte_cardex")
@require_GET
def reporte_cardex(request, formato=None):
    formulario = ReporteCardexForm(request.GET)
    valido = formulario.is_valid()
    filtros = formulario.cleaned_data if valido else {}
    with timezone.override("America/Santiago"):
        movimientos = movimientos_cardex(filtros)
        if formato:
            if not valido:
                return HttpResponseBadRequest("Corrige los filtros del Cardex antes de exportar.")
            filas = list(filas_cardex(movimientos.iterator()))
            if formato == "xlsx":
                return export_workbook("Cardex de materiales", CARDEX_COLUMNAS, filas, None, filtros, "cardex.xlsx")
            return export_pdf("Cardex de materiales", request.user, CARDEX_COLUMNAS, filas, None, filtros, "cardex.pdf")

        pagina = Paginator(movimientos if valido else movimientos.none(), 30).get_page(request.GET.get("pagina"))
        filas = list(filas_cardex(pagina.object_list))
    contexto = _navigation_context(request)
    contexto.update(
        titulo_panel="Cardex de materiales", report_type="cardex", formulario=formulario,
        pagina=pagina, filas=filas, columnas=CARDEX_COLUMNAS, filtros_validos=valido,
    )
    return render(request, "reportes/reporte_cardex.html", contexto)


def _report_context(request, report, form_class, title):
    context = _navigation_context(request)
    if not context["perm"][f"reporte_{report}"]:
        raise PermissionDenied("Tu rol no tiene permiso para este reporte.")
    service = ReportQueryService(request)
    result = service.get(report, form_class)
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
        request, "ingresos", ReporteIngresosForm, "Reporte de ingresos"
    )
    return render(request, "reportes/reporte_ingresos.html", context)


@login_required
def reporte_egresos(request):
    context, _ = _report_context(
        request, "egresos", ReporteEgresosForm, "Reporte de egresos"
    )
    return render(request, "reportes/reporte_egresos.html", context)


@login_required
def reporte_inventario(request):
    context, _ = _report_context(
        request, "inventario", ReporteInventarioForm, "Reporte de inventario"
    )
    return render(request, "reportes/reporte_inventario.html", context)


@login_required
def exportar_ingresos_excel(request):
    _authorize(request, "ingresos")
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
    _authorize(request, "ingresos")
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
    _authorize(request, "egresos")
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
    _authorize(request, "egresos")
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
    _authorize(request, "inventario")
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
    _authorize(request, "inventario")
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
