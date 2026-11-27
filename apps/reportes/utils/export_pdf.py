from io import BytesIO
from datetime import datetime

from django.http import HttpResponse
from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


def _pdf_value(value):
    if isinstance(value, datetime):
        if timezone.is_aware(value):
            value = timezone.localtime(value)
        return value.strftime("%d/%m/%Y %H:%M")
    return value


def _filters_text(filters):
    names = (
        ("fecha_desde", "Desde"), ("fecha_hasta", "Hasta"),
        ("proveedor", "Proveedor"), ("articulo", "Artículo"),
        ("tipo_documento", "Tipo documento"), ("tipo", "Tipo"),
        ("obra", "Obra"), ("categoria", "Categoría"),
        ("estado", "Estado"), ("estado_stock", "Estado stock"), ("q", "Búsqueda"),
    )
    items = []
    for key, label in names:
        value = filters.get(key)
        if value not in (None, ""):
            items.append(f"{label}: {value}")
    return " | ".join(items) or "Sin filtros"


def export_pdf(title, user, headers, rows, totals_row, filters, filename):
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=12 * mm,
        rightMargin=12 * mm,
        topMargin=12 * mm,
        bottomMargin=12 * mm,
        title=title,
    )
    styles = getSampleStyleSheet()
    emitted = timezone.localtime(timezone.now()).strftime("%d/%m/%Y %H:%M")
    username = user.get_full_name() or user.get_username()
    content = [
        Paragraph("Logística FYAG", styles["Title"]),
        Paragraph(title, styles["Heading2"]),
        Paragraph(f"Emitido: {emitted} · Usuario: {username}", styles["Normal"]),
        Paragraph(f"Filtros: {_filters_text(filters)}", styles["Normal"]),
        Spacer(1, 6 * mm),
    ]

    header_style = ParagraphStyle(
        "ReportTableHeader",
        parent=styles["BodyText"],
        textColor=colors.white,
        fontName="Helvetica-Bold",
    )
    data = [[Paragraph(str(header), header_style) for header in headers]]
    for row in rows:
        data.append([
            Paragraph("" if value is None else str(_pdf_value(value)), styles["BodyText"])
            for value in row
        ])
    if totals_row:
        data.append([
            Paragraph(str(_pdf_value(value)), styles["BodyText"])
            for value in totals_row
        ])

    table = Table(data, repeatRows=1, hAlign="LEFT")
    table_style = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#10224C")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#CBD5E1")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, colors.HexColor("#F1F5F9")]),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    if totals_row:
        table_style += [
            ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#DBEAFE")),
            ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ]
    table.setStyle(TableStyle(table_style))
    content.append(table)
    doc.build(content)

    response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    buffer.close()
    return response
