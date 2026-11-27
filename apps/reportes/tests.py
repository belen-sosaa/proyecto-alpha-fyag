from datetime import date
from decimal import Decimal
from io import BytesIO

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from openpyxl import load_workbook
from pypdf import PdfReader

from apps.bodega.models import Articulo, Categoria, MovimientoCardex, Obra
from apps.compras.models import DetalleFactura, EntidadComercial, FacturaCompra
from apps.cuentas.roles import (
    ADMIN_BODEGA,
    AUDITOR,
    BODEGUERO,
    FINANZAS,
    GERENTE,
)


class ReportesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.usuarios = {}
        for rol in (GERENTE, AUDITOR, FINANZAS, ADMIN_BODEGA, BODEGUERO):
            grupo = Group.objects.create(name=rol)
            usuario = get_user_model().objects.create_user(
                username=f"report_{rol.lower().replace(' ', '_')}",
                password="ClaveSegura2026!",
            )
            usuario.groups.add(grupo)
            cls.usuarios[rol] = usuario

        cls.categoria = Categoria.objects.create(nombre="Refrigeración")
        cls.otra_categoria = Categoria.objects.create(nombre="Electricidad")
        cls.articulo = Articulo.objects.create(
            sku="REP-100",
            descripcion="Filtro principal",
            categoria=cls.categoria,
            unidad_medida="unidad",
            ubicacion_fisica="A-01",
            stock_fisico=12,
            stock_minimo=5,
            stock_critico=3,
            precio_lista=Decimal("20.00"),
        )
        cls.articulo_bajo = Articulo.objects.create(
            sku="ELE-200",
            descripcion="Cable eléctrico",
            categoria=cls.otra_categoria,
            unidad_medida="metro",
            ubicacion_fisica="B-02",
            stock_fisico=4,
            stock_minimo=8,
            stock_critico=2,
            precio_lista=Decimal("5.00"),
        )
        cls.proveedor = EntidadComercial.objects.create(
            rut="99999997-2",
            razon_social="Proveedor Norte",
            tipo_entidad="PROVEEDOR",
        )
        cls.factura = FacturaCompra.objects.create(
            proveedor=cls.proveedor,
            folio="FAC-2026-001",
            tipo_documento=FacturaCompra.TipoDocumento.FACTURA,
            fecha_emision=date(2026, 11, 20),
            subtotal_neto=Decimal("100.00"),
            iva=Decimal("19.00"),
            total=Decimal("119.00"),
            usuario_registro=cls.usuarios[FINANZAS],
            estado_ingreso=FacturaCompra.EstadoIngreso.CONFIRMADO,
        )
        DetalleFactura.objects.create(
            factura=cls.factura,
            articulo=cls.articulo,
            cantidad=5,
            precio_unitario_neto=Decimal("20.00"),
            costo_unitario_neto_final=Decimal("20.00"),
        )
        cls.obra = Obra.objects.create(nombre="Obra Norte")
        cls.despacho = MovimientoCardex.objects.create(
            tipo=MovimientoCardex.Tipo.DESPACHO,
            cantidad=2,
            articulo=cls.articulo,
            usuario=cls.usuarios[BODEGUERO],
            obra=cls.obra,
            stock_resultante=10,
            justificacion="Salida a obra",
            documento_referencia="DESP-1",
        )

    def login_as(self, rol):
        self.client.force_login(self.usuarios[rol])

    def test_ingresos_filtra_por_fecha_tipo_estado_proveedor_y_articulo(self):
        self.login_as(GERENTE)
        response = self.client.get(reverse("reportes:ingresos"), {
            "fecha_desde": "2026-11-01",
            "fecha_hasta": "2026-11-30",
            "proveedor": self.proveedor.pk,
            "tipo_documento": "FACTURA",
            "estado": "CONFIRMADO",
            "articulo": "REP",
        })
        self.assertEqual(response.status_code, 200)
        result = response.context["reporte"]
        self.assertEqual(len(result["rows"]), 1)
        self.assertEqual(result["rows"][0]["folio"], "FAC-2026-001")
        self.assertEqual(result["totals"]["cantidad"], 5)
        self.assertContains(response, "FAC-2026-001")

    def test_egresos_filtra_por_tipo_obra_y_articulo(self):
        self.login_as(AUDITOR)
        response = self.client.get(reverse("reportes:egresos"), {
            "tipo": "DESPACHO",
            "obra": "Norte",
            "articulo": "REP",
        })
        self.assertEqual(response.status_code, 200)
        result = response.context["reporte"]
        self.assertEqual(len(result["rows"]), 1)
        self.assertEqual(result["rows"][0]["tipo"], "DESPACHO")
        self.assertEqual(result["totals"]["general"], 2)

    def test_egresos_excluye_movimientos_de_ingreso(self):
        MovimientoCardex.objects.create(
            tipo=MovimientoCardex.Tipo.INGRESO,
            cantidad=25,
            articulo=self.articulo,
            usuario=self.usuarios[FINANZAS],
            stock_resultante=37,
            documento_referencia="ING-EXCLUIR",
        )
        self.login_as(AUDITOR)

        response = self.client.get(reverse("reportes:egresos"))

        self.assertEqual(response.status_code, 200)
        result = response.context["reporte"]
        self.assertEqual(result["totals"]["general"], 2)
        self.assertNotIn("ING-EXCLUIR", [row["referencia"] for row in result["rows"]])

    def test_egresos_excel_exporta_fechas_aware_sin_zona_horaria(self):
        self.login_as(AUDITOR)
        movimiento = MovimientoCardex.objects.get(pk=self.despacho.pk)
        self.assertTrue(timezone.is_aware(movimiento.fecha_hora))

        response = self.client.get(reverse("reportes:egresos_excel"))

        self.assertEqual(response.status_code, 200)
        workbook = load_workbook(BytesIO(response.content))
        value = workbook.active["A7"].value
        self.assertIsNone(value.tzinfo)

    def test_egresos_pdf_no_expone_offset_ni_microsegundos(self):
        self.login_as(AUDITOR)

        response = self.client.get(reverse("reportes:egresos_pdf"))

        self.assertEqual(response.status_code, 200)
        reader = PdfReader(BytesIO(response.content))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        self.assertNotIn("+00:00", text)
        self.assertNotRegex(text, r"\.\d{6}")

    def test_inventario_filtra_por_categoria_estado_y_busqueda(self):
        self.login_as(AUDITOR)
        response = self.client.get(reverse("reportes:inventario"), {
            "categoria": self.categoria.pk,
            "estado_stock": "normal",
            "q": "REP",
        })
        self.assertEqual(response.status_code, 200)
        result = response.context["reporte"]
        self.assertEqual(result["totals"]["articulos"], 1)
        self.assertEqual(result["totals"]["unidades"], 12)
        self.assertEqual(result["totals"]["valorizacion"], Decimal("240.00"))

    def test_roles_autorizados_y_bloqueo_403(self):
        for rol in (GERENTE, AUDITOR, FINANZAS, ADMIN_BODEGA):
            self.login_as(rol)
            self.assertEqual(self.client.get(reverse("reportes:ingresos")).status_code, 200)
        self.login_as(BODEGUERO)
        self.assertEqual(self.client.get(reverse("reportes:ingresos")).status_code, 403)

        for rol in (GERENTE, AUDITOR, ADMIN_BODEGA, BODEGUERO):
            self.login_as(rol)
            self.assertEqual(self.client.get(reverse("reportes:egresos")).status_code, 200)
            self.assertEqual(self.client.get(reverse("reportes:inventario")).status_code, 200)

    def test_menu_muestra_reportes_segun_los_permisos_del_rol(self):
        reportes_por_rol = {
            GERENTE: ("ingresos", "egresos", "inventario"),
            AUDITOR: ("ingresos", "egresos", "inventario"),
            FINANZAS: ("ingresos",),
            ADMIN_BODEGA: ("ingresos", "egresos", "inventario"),
            BODEGUERO: ("egresos", "inventario"),
        }
        for rol, reportes_visibles in reportes_por_rol.items():
            with self.subTest(rol=rol):
                self.login_as(rol)
                pagina = self.client.get(reverse("inicio"))
                for reporte in ("ingresos", "egresos", "inventario"):
                    enlace = f'href="{reverse("reportes:" + reporte)}"'
                    if reporte in reportes_visibles:
                        self.assertContains(pagina, enlace)
                    else:
                        self.assertNotContains(pagina, enlace)

    def test_bodeguero_no_ve_costos_en_html_excel_pdf(self):
        self.login_as(BODEGUERO)
        page = self.client.get(reverse("reportes:inventario"))
        self.assertNotContains(page, "Valorización")
        self.assertNotContains(page, "Precio Lista")

        excel = self.client.get(reverse("reportes:inventario_excel"))
        wb = load_workbook(BytesIO(excel.content))
        ws = wb.active
        headers = [cell.value for cell in ws[6]]
        self.assertNotIn("Precio Lista", headers)
        self.assertNotIn("Valorización", headers)

        pdf = self.client.get(reverse("reportes:inventario_pdf"))
        reader = PdfReader(BytesIO(pdf.content))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        self.assertNotIn("Precio Lista", text)
        self.assertNotIn("Valorización", text)

    def test_exportaciones_excel_y_pdf_contienen_mismos_registros(self):
        self.login_as(GERENTE)
        filtros = "fecha_desde=2026-11-01&fecha_hasta=2026-11-30&tipo_documento=FACTURA"

        page = self.client.get(reverse("reportes:ingresos") + "?" + filtros)
        rows = page.context["reporte"]["rows"]
        self.assertEqual(len(rows), 1)

        excel = self.client.get(reverse("reportes:ingresos_excel") + "?" + filtros)
        self.assertEqual(excel["Content-Type"], "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        wb = load_workbook(BytesIO(excel.content))
        ws = wb.active
        excel_values = [ws.cell(row=7, column=3).value]
        self.assertEqual(excel_values[0], "FAC-2026-001")
        self.assertIn("Filtros:", "\n".join(str(cell.value) for row in ws.iter_rows() for cell in row if cell.value))

        pdf = self.client.get(reverse("reportes:ingresos_pdf") + "?" + filtros)
        self.assertEqual(pdf["Content-Type"], "application/pdf")
        reader = PdfReader(BytesIO(pdf.content))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        self.assertIn("FAC-2026-001", text)
        self.assertIn("Reporte de ingresos", text)
