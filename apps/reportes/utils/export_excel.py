from datetime import datetime, time

from django.http import HttpResponse
from django.utils import timezone
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter


def _excel_value(value):
    if isinstance(value, datetime):
        if timezone.is_aware(value):
            value = timezone.localtime(value)
        return value.replace(tzinfo=None)
    if isinstance(value, time):
        if value.utcoffset() is not None:
            local_datetime = timezone.localtime(
                datetime.combine(timezone.localdate(), value)
            )
            return local_datetime.time().replace(tzinfo=None)
        return value.replace(tzinfo=None)
    return value


def _filters_text(filters):
    names = {
        "fecha_desde": "Desde", "fecha_hasta": "Hasta",
        "proveedor": "Proveedor", "articulo": "Artículo",
        "tipo_documento": "Tipo documento", "tipo": "Tipo",
        "obra": "Obra", "categoria": "Categoría",
        "usuario": "Usuario", "conteo": "Conteo", "observacion": "Observación",
        "estado": "Estado", "estado_stock": "Estado stock", "q": "Búsqueda",
    }
    items = []
    for key, label in names.items():
        value = filters.get(key)
        if value not in (None, ""):
            items.append(f"{label}: {value}")
    return " | ".join(items) or "Sin filtros"


def export_workbook(title, headers, rows, totals, filters, filename, ver_costos=True):
    wb = Workbook()
    ws = wb.active
    ws.title = title[:31]
    ws.append(["Logística FYAG"])
    ws["A1"].font = Font(bold=True, size=16, color="FFFFFF")
    ws["A1"].fill = PatternFill("solid", fgColor="10224C")
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=max(1, len(headers)))
    ws.append([title])
    ws.append([f"Emitido: {timezone.localtime(timezone.now()).strftime('%d/%m/%Y %H:%M')}"])
    ws.append([f"Filtros: {_filters_text(filters)}"])

    ws.append([])
    header_row = ws.max_row + 1
    ws.append(headers)
    for cell in ws[header_row]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="2563EB")
        cell.alignment = Alignment(horizontal="center")

    for row in rows:
        ws.append([_excel_value(value) for value in row])
        for cell in ws[ws.max_row]:
            if isinstance(cell.value, str):
                cell.data_type = "s"

    if totals:
        ws.append([])
        ws.append([_excel_value(value) for value in totals])

    for index in range(1, ws.max_column + 1):
        column_letter = get_column_letter(index)
        maximum = max(
            len(str(ws.cell(row=row, column=index).value or ""))
            for row in range(1, ws.max_row + 1)
        )
        ws.column_dimensions[column_letter].width = min(max(maximum + 2, 12), 45)

    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    wb.save(response)
    return response
