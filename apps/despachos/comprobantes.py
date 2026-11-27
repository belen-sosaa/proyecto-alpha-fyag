from io import BytesIO
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

from reportlab.graphics.shapes import Circle, Drawing, PolyLine, Rect
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer


def generar_comprobante_firmado(despacho, recepcion):
    titulo = f"Comprobante de despacho #{despacho.pk}"
    # El PDF se arma en memoria y se entrega en la descarga.
    buffer = BytesIO()
    documento = SimpleDocTemplate(
        buffer, pagesize=A4, title=titulo, author="Frío y Agroindustria SpA",
        leftMargin=54, rightMargin=54, topMargin=48, bottomMargin=48,
    )
    estilos = getSampleStyleSheet()
    tinta = colors.HexColor("#14213d")
    encabezado = ParagraphStyle(
        "EncabezadoComprobante", parent=estilos["Title"], alignment=0, textColor=tinta,
    )
    etiqueta = ParagraphStyle(
        "EtiquetaComprobante", parent=estilos["Heading4"],
        textColor=colors.HexColor("#475569"), spaceBefore=0, spaceAfter=2,
    )
    valor = ParagraphStyle(
        "ValorComprobante", parent=estilos["Normal"], textColor=tinta, leading=15,
    )
    zona = ZoneInfo("America/Santiago")
    campos = (
        ("Fecha de despacho", despacho.fecha_despacho.astimezone(zona).strftime("%d/%m/%Y %H:%M")),
        ("Obra", despacho.obra.nombre),
        ("Artículo", f"{despacho.articulo.sku} · {despacho.articulo.descripcion}"),
        ("Cantidad", f"{despacho.cantidad} {despacho.articulo.unidad_medida}"),
        ("Responsable del despacho", despacho.usuario.get_full_name() or despacho.usuario.username),
        ("Recibido por", recepcion.nombre_receptor),
        ("Fecha de recepción", recepcion.fecha_recepcion.astimezone(zona).strftime("%d/%m/%Y %H:%M")),
    )
    contenido = [
        Paragraph("Frío y Agroindustria SpA", etiqueta),
        Paragraph(titulo, encabezado),
        Spacer(1, 14),
    ]
    # Conservamos cada etiqueta con su valor y tratamos símbolos como & como parte del texto.
    for nombre, dato in campos:
        contenido.append(KeepTogether([
            Paragraph(nombre, etiqueta),
            Paragraph(escape(dato), valor),
            Spacer(1, 12),
        ]))

    escala = 0.65
    ancho, alto = 600 * escala, 220 * escala
    firma = Drawing(ancho, alto)
    firma.add(Rect(0, 0, ancho, alto, fillColor=colors.HexColor("#f8fafc"),
                   strokeColor=colors.HexColor("#cbd5e1"), rx=6, ry=6))
    for trazo in recepcion.firma:
        # Un punto alto en el dibujo debe seguir arriba en el PDF, aunque allí el eje vertical parte desde abajo.
        puntos = [(x * escala, alto - y * escala) for x, y in trazo]
        if len(puntos) == 1:
            firma.add(Circle(*puntos[0], 1, fillColor=tinta, strokeColor=None))
        else:
            firma.add(PolyLine(puntos, strokeColor=tinta, strokeWidth=1.8,
                              strokeLineCap=1, strokeLineJoin=1))
    contenido.append(KeepTogether([Paragraph("Firma de recepción", etiqueta), firma]))
    documento.build(contenido)
    # Volvemos al principio para que la descarga lea el archivo completo.
    buffer.seek(0)
    return buffer
