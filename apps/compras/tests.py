from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import IntegrityError
from django.test import TestCase
from django.urls import reverse

from apps.bodega.models import Articulo, BitacoraAuditoria, Categoria, MovimientoCardex
from apps.cuentas.roles import ADMIN_BODEGA, BODEGUERO, FINANZAS
from .models import DetalleFactura, EntidadComercial, FacturaCompra


class IngresoFacturaTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.usuarios = {}
        for rol in (ADMIN_BODEGA, BODEGUERO, FINANZAS):
            usuario = get_user_model().objects.create_user(username=rol)
            usuario.groups.add(Group.objects.create(name=rol))
            cls.usuarios[rol] = usuario
        cls.proveedor = EntidadComercial.objects.create(rut="11111111-1", razon_social="Proveedor QA")
        categoria = Categoria.objects.create(nombre="Repuestos")
        cls.articulo = Articulo.objects.create(sku="QA-1", descripcion="Filtro", categoria=categoria, stock_fisico=10)
        cls.otro_articulo = Articulo.objects.create(sku="QA-2", descripcion="Válvula", categoria=categoria, stock_fisico=2)

    def registrar(self):
        return self.client.post(reverse("registrar_factura"), {
            "proveedor_id": self.proveedor.pk,
            "folio": "00001",
            "fecha_emision": "2026-11-19",
            "detalles": [
                {"articulo_id": self.articulo.pk, "cantidad": 2, "precio_unitario_neto": "100.00",
                 "descuento_1": "10", "descuento_2": "5", "descuento_3": "2"},
                {"articulo_id": self.otro_articulo.pk, "cantidad": 3, "precio_unitario_neto": "20.00"},
            ],
        }, content_type="application/json")

    def confirmar(self, factura_id):
        return self.client.post(reverse("confirmar_ingreso"), {"factura_id": factura_id},
                                content_type="application/json")

    def registrar_documento(self, tipo, folio, lineas, origen=None, proveedor=None):
        return self.client.post(reverse("registrar_factura"), {
            "proveedor_id": (proveedor or self.proveedor).pk, "folio": folio,
            "tipo_documento": tipo, "fecha_emision": "2026-11-22", "documento_origen_id": origen,
            "detalles": lineas,
        }, content_type="application/json")

    def test_factura_web_con_varias_lineas_y_precio_de_lista_al_confirmar(self):
        self.client.force_login(self.usuarios[ADMIN_BODEGA])
        vista = reverse("vista_registrar_compra")
        self.assertContains(self.client.get(vista), "detalles-TOTAL_FORMS")
        respuesta = self.client.post(vista, {
            "proveedor_id": self.proveedor.pk, "folio": "WEB-VARIAS", "fecha_emision": "2026-11-22",
            "tipo_documento": "FACTURA", "detalles-TOTAL_FORMS": 2, "detalles-INITIAL_FORMS": 0,
            "detalles-0-articulo_id": self.articulo.pk, "detalles-0-cantidad": 3,
            "detalles-0-precio_unitario_neto": "100.00", "detalles-0-descuento_1": "10",
            "detalles-0-precio_lista": "140.00",
            "detalles-1-articulo_id": self.otro_articulo.pk, "detalles-1-cantidad": 1,
            "detalles-1-precio_unitario_neto": "20.00",
        })
        self.assertRedirects(respuesta, reverse("compras"))
        factura = FacturaCompra.objects.get(folio="WEB-VARIAS")
        self.assertEqual(factura.detalles.count(), 2)
        self.assertEqual(str(factura.total), "345.10")
        self.articulo.refresh_from_db()
        self.assertIsNone(self.articulo.precio_lista)
        self.assertEqual(self.confirmar(factura.pk).status_code, 201)
        self.articulo.refresh_from_db()
        self.assertEqual(self.articulo.stock_fisico, 13)
        self.assertEqual(str(self.articulo.precio_lista), "140.00")
        self.otro_articulo.refresh_from_db()
        self.assertIsNone(self.otro_articulo.precio_lista)
        self.assertTrue(BitacoraAuditoria.objects.filter(
            accion="COMPRA", entidad="Articulo", id_registro=self.articulo.pk,
        ).exists())

    def test_guia_y_factura_asociada_actualizan_existencias_una_sola_vez(self):
        self.client.force_login(self.usuarios[ADMIN_BODEGA])
        linea = {"articulo_id": self.articulo.pk, "cantidad": 4}
        registro = self.registrar_documento("GUIA_DESPACHO", "GUIA-1", [linea])
        self.assertEqual(registro.status_code, 201, registro.content)
        guia_id = registro.json()["id"]
        self.assertEqual(registro.json()["iva"], "0.00")
        self.assertEqual(self.confirmar(guia_id).status_code, 201)
        datos_factura = {**linea, "precio_unitario_neto": "100", "precio_lista": "150"}
        distinta = self.registrar_documento("FACTURA", "N-2", [{**datos_factura, "cantidad": 3}], guia_id)
        self.assertEqual(distinta.status_code, 400)
        registro = self.registrar_documento("FACTURA", "N-2", [datos_factura], guia_id)
        self.assertEqual(registro.status_code, 201, registro.content)
        duplicada = self.registrar_documento("FACTURA", "N-3", [datos_factura], guia_id)
        self.assertEqual(duplicada.status_code, 409)
        self.assertEqual(duplicada.json()["codigo"], "GUIA_YA_FACTURADA")
        confirmacion = self.confirmar(registro.json()["id"])
        self.assertEqual(confirmacion.status_code, 201, confirmacion.content)
        self.assertEqual(confirmacion.json()["movimientos"], [])
        self.articulo.refresh_from_db()
        self.assertEqual(self.articulo.stock_fisico, 14)
        self.assertEqual(str(self.articulo.precio_lista), "150.00")
        self.assertEqual(MovimientoCardex.objects.get().documento_referencia, "Guía de despacho GUIA-1")

    def test_nota_de_credito_limita_stock_e_importe_y_conserva_la_factura(self):
        self.client.force_login(self.usuarios[ADMIN_BODEGA])
        linea = {"articulo_id": self.articulo.pk, "cantidad": 4, "precio_unitario_neto": "100"}
        origen = self.registrar_documento("FACTURA", "ORIGEN", [linea]).json()["id"]
        self.assertEqual(self.confirmar(origen).status_code, 201)
        sin_origen = self.registrar_documento("NOTA_CREDITO", "C-0", [linea])
        self.assertEqual(sin_origen.status_code, 400)
        proveedor_ajeno = EntidadComercial.objects.create(rut="22222222-2", razon_social="Otro proveedor")
        self.assertEqual(self.registrar_documento(
            "NOTA_CREDITO", "C-0", [linea], origen, proveedor_ajeno,
        ).status_code, 400)
        exceso = self.registrar_documento("NOTA_CREDITO", "C-0", [{**linea, "precio_unitario_neto": "101"}], origen)
        self.assertEqual(exceso.status_code, 400)
        lineas_credito = [{**linea, "cantidad": 3}]
        primero = self.registrar_documento("NOTA_CREDITO", "C-1", lineas_credito, origen).json()["id"]
        segundo = self.registrar_documento("NOTA_CREDITO", "C-2", lineas_credito, origen).json()["id"]
        self.assertEqual(self.client.get(reverse("finanzas")).context["resumen"]["total"], Decimal("476.00"))

        self.client.force_login(self.usuarios[BODEGUERO])
        Articulo.objects.filter(pk=self.articulo.pk).update(stock_fisico=2)
        respuesta = self.confirmar(primero)
        self.assertEqual(respuesta.status_code, 409)
        self.assertEqual(respuesta.json()["codigo"], "STOCK_FUERA_DE_RANGO")
        self.assertEqual(FacturaCompra.objects.get(pk=primero).estado_ingreso, "PENDIENTE")
        self.assertFalse(MovimientoCardex.objects.filter(tipo=MovimientoCardex.Tipo.REVERSA).exists())
        Articulo.objects.filter(pk=self.articulo.pk).update(stock_fisico=14)
        respuesta = self.confirmar(primero)
        self.assertEqual(respuesta.status_code, 201, respuesta.content)
        self.assertEqual(self.confirmar(primero).json()["codigo"], "INGRESO_YA_CONFIRMADO")
        self.assertEqual(self.confirmar(segundo).json()["codigo"], "DOCUMENTO_INVALIDO")
        self.articulo.refresh_from_db()
        self.assertEqual(self.articulo.stock_fisico, 11)
        movimiento = MovimientoCardex.objects.get(tipo=MovimientoCardex.Tipo.REVERSA)
        self.assertEqual(movimiento.stock_resultante, 11)
        self.assertEqual(movimiento.proveedor, self.proveedor)
        self.assertEqual(FacturaCompra.objects.get(pk=origen).detalles.get().cantidad, 4)
        self.client.force_login(self.usuarios[ADMIN_BODEGA])
        self.assertEqual(self.client.get(reverse("finanzas")).context["resumen"]["total"], Decimal("119.00"))

    def test_factura_y_confirmacion_guardan_datos_y_bloquean_un_ingreso_duplicado(self):
        self.client.force_login(self.usuarios[FINANZAS])
        registro = self.registrar()
        self.assertEqual(registro.status_code, 201)
        datos = registro.json()
        self.assertEqual(datos["detalles"][0]["costo_unitario_neto_final"], "83.79")
        self.assertEqual(datos["subtotal_neto"], "227.58")
        self.assertEqual(datos["iva"], "43.24")
        self.assertEqual(datos["total"], "270.82")
        self.assertEqual(self.registrar().status_code, 409)
        self.articulo.refresh_from_db()
        self.assertEqual(self.articulo.stock_fisico, 10)

        self.client.force_login(self.usuarios[BODEGUERO])
        with patch("apps.compras.views.guardar_respaldo_cardex") as respaldo:
            with self.captureOnCommitCallbacks(execute=True) as callbacks:
                respuesta = self.confirmar(datos["id"])
                respaldo.assert_not_called()
            respaldo.assert_called_once()
        self.assertEqual(len(callbacks), 1)
        self.assertEqual(respuesta.status_code, 201)
        movimientos = respuesta.json()["movimientos"]
        self.assertEqual([movimiento["stock_resultante"] for movimiento in movimientos], [12, 5])
        self.assertEqual([movimiento["tipo"] for movimiento in movimientos], ["INGRESO", "INGRESO"])
        evento = BitacoraAuditoria.objects.get(id_registro=movimientos[0]["id"], entidad="MovimientoCardex")
        self.assertEqual(evento.valor_anterior, {"stock_fisico": 10})
        self.assertEqual(evento.valor_nuevo["factura_id"], datos["id"])
        self.assertEqual(evento.valor_nuevo["detalle_factura_id"], datos["detalles"][0]["id"])
        self.assertEqual(respaldo.call_args.args[0][0]["movimiento_id"], movimientos[0]["id"])

        segunda = self.confirmar(datos["id"])
        self.assertEqual(segunda.status_code, 409)
        self.assertEqual(segunda.json()["codigo"], "INGRESO_YA_CONFIRMADO")
        self.articulo.refresh_from_db()
        self.assertEqual(self.articulo.stock_fisico, 12)
        self.assertEqual(MovimientoCardex.objects.count(), 2)

    def test_si_falla_la_segunda_linea_se_revierte_toda_la_confirmacion(self):
        self.client.force_login(self.usuarios[ADMIN_BODEGA])
        factura_id = self.registrar().json()["id"]
        crear_evento = BitacoraAuditoria.objects.create

        def fallar_segunda_linea(**datos):
            if datos["valor_nuevo"]["articulo_id"] == self.otro_articulo.pk:
                raise IntegrityError("Fallo simulado")
            return crear_evento(**datos)

        with (
            patch("apps.compras.views.BitacoraAuditoria.objects.create", side_effect=fallar_segunda_linea),
            self.captureOnCommitCallbacks(execute=True) as callbacks,
            self.assertLogs("apps.compras.views", level="ERROR"),
        ):
            respuesta = self.confirmar(factura_id)
        self.assertEqual(respuesta.status_code, 500)
        self.assertEqual(callbacks, [])
        self.articulo.refresh_from_db()
        self.otro_articulo.refresh_from_db()
        self.assertEqual((self.articulo.stock_fisico, self.otro_articulo.stock_fisico), (10, 2))
        self.assertFalse(MovimientoCardex.objects.exists())
        self.assertFalse(BitacoraAuditoria.objects.exists())
        self.assertEqual(FacturaCompra.objects.get(pk=factura_id).estado_ingreso, FacturaCompra.EstadoIngreso.PENDIENTE)

    def test_proveedores_sesion_permisos_y_facturas_historicas(self):
        self.assertEqual(self.registrar().status_code, 401)
        self.client.force_login(self.usuarios[BODEGUERO])
        self.assertEqual(self.registrar().status_code, 403)
        proveedores = self.client.get(reverse("listar_proveedores"), {"q": "QA"}).json()
        self.assertEqual([proveedor["id"] for proveedor in proveedores], [self.proveedor.pk])
        self.client.force_login(self.usuarios[FINANZAS])
        factura_id = self.registrar().json()["id"]
        self.assertEqual(self.confirmar(factura_id).status_code, 403)
        FacturaCompra.objects.filter(pk=factura_id).update(estado_ingreso=FacturaCompra.EstadoIngreso.HISTORICO)
        self.client.force_login(self.usuarios[BODEGUERO])
        respuesta = self.confirmar(factura_id)
        self.assertEqual(respuesta.status_code, 409)
        self.assertEqual(respuesta.json()["codigo"], "FACTURA_HISTORICA")


    def test_formulario_registra_factura_y_bodega_confirma_sin_duplicar_stock(self):
        self.client.force_login(self.usuarios[FINANZAS])
        ruta = reverse("vista_registrar_compra")
        self.assertContains(self.client.get(ruta), "Registrar factura")
        respuesta = self.client.post(ruta, {
            "proveedor_id": self.proveedor.pk, "folio": "FORM-QA", "fecha_emision": "2026-11-22",
            "articulo_id": self.articulo.pk, "cantidad": 2, "precio_unitario_neto": "100.00",
            "descuento_1": "10", "descuento_2": "5",
        })
        self.assertRedirects(respuesta, reverse("compras"))
        factura = FacturaCompra.objects.get(folio="FORM-QA")
        self.assertEqual(factura.estado_ingreso, FacturaCompra.EstadoIngreso.PENDIENTE)
        self.assertEqual(Articulo.objects.get(pk=self.articulo.pk).stock_fisico, 10)

        ruta_ingreso = reverse("vista_confirmar_compra", args=[factura.pk])
        self.assertEqual(self.client.post(ruta_ingreso).status_code, 403)
        self.client.force_login(self.usuarios[BODEGUERO])
        self.assertContains(self.client.get(reverse("compras")), ruta_ingreso)
        self.assertRedirects(self.client.post(ruta_ingreso), reverse("compras"))
        self.assertRedirects(self.client.post(ruta_ingreso), reverse("compras"))
        self.assertEqual(Articulo.objects.get(pk=self.articulo.pk).stock_fisico, 12)

    def test_excel_lee_compras_reconciliadas_y_restringe_costos(self):
        from io import BytesIO

        from openpyxl import load_workbook

        self.client.force_login(self.usuarios[FINANZAS])
        self.assertEqual(self.registrar().status_code, 201)
        ruta = reverse("articulo_exportar_precios_excel", args=[self.articulo.pk])
        respuesta = self.client.get(ruta)
        self.assertEqual(respuesta.status_code, 200)
        hoja = load_workbook(BytesIO(respuesta.content), read_only=True).active
        self.assertEqual(hoja["B2"].value, "00001")
        self.assertEqual(hoja["F2"].value, 83.79)
        self.client.force_login(self.usuarios[BODEGUERO])
        self.assertEqual(self.client.get(ruta).status_code, 403)



class UltimosPreciosTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.admin = User.objects.create_user(username="admin-precios")
        cls.admin.groups.add(Group.objects.create(name=ADMIN_BODEGA))
        cls.bodeguero = User.objects.create_user(username="bodega-precios")
        cls.bodeguero.groups.add(Group.objects.create(name=BODEGUERO))
        cls.proveedor = EntidadComercial.objects.create(rut="11111111-1", razon_social="Proveedor QA")
        categoria = Categoria.objects.create(nombre="Repuestos")
        cls.articulo = Articulo.objects.create(sku="QA-1", descripcion="Filtro", categoria=categoria)
        cls.otro_articulo = Articulo.objects.create(sku="QA-2", descripcion="Válvula", categoria=categoria)

    def crear_compra(self, folio, fecha, articulo, costo, tipo_documento="FACTURA"):
        factura = FacturaCompra.objects.create(
            proveedor=self.proveedor, folio=folio, fecha_emision=fecha,
            tipo_documento=tipo_documento, usuario_registro=self.admin, subtotal_neto=costo, iva=0, total=costo,
        )
        DetalleFactura.objects.create(
            factura=factura, articulo=articulo, cantidad=1,
            precio_unitario_neto=costo, costo_unitario_neto_final=costo,
        )
        return factura

    def test_de_cinco_compras_devuelve_tres_facturas_distintas_en_orden(self):
        facturas = [self.crear_compra(str(i), fecha, self.articulo, i) for i, fecha in enumerate((
            "2026-11-20", "2026-11-15", "2026-11-19", "2026-11-20", "2026-11-17",
        ), start=1)]
        DetalleFactura.objects.create(
            factura=facturas[3], articulo=self.articulo, cantidad=2,
            precio_unitario_neto=90, costo_unitario_neto_final=80,
        )
        self.crear_compra("NC-1", "2026-11-21", self.articulo, 100, "NOTA_CREDITO")
        otra = self.crear_compra("OTRA", "2026-11-21", self.otro_articulo, 200)

        self.client.force_login(self.admin)
        respuesta = self.client.get(reverse("api_ultimos_precios", args=[self.articulo.pk]))
        self.assertEqual(respuesta.status_code, 200)
        precios = respuesta.json()
        self.assertEqual([precio["factura_id"] for precio in precios], [facturas[3].pk, facturas[0].pk, facturas[2].pk])
        self.assertEqual(precios[0], {
            "factura_id": facturas[3].pk, "folio": "4", "proveedor_id": self.proveedor.pk,
            "fecha_emision": "2026-11-20", "costo_unitario_neto_final": "80.00",
        })
        filtrada = self.client.get(reverse("api_ultimos_precios", args=[self.otro_articulo.pk])).json()
        self.assertEqual([precio["factura_id"] for precio in filtrada], [otra.pk])

        pagina = self.client.get(reverse("ultimos_precios"), {"articulo": self.articulo.pk})
        self.assertContains(pagina, "Proveedor QA", count=3)
        self.assertContains(pagina, "80.00")
        self.assertNotContains(pagina, "Factura NC-1")
        self.assertContains(self.client.get(reverse("catalogo_listar")), "Últimos precios")

        detalle_url = reverse("articulo_detalle", args=[self.articulo.pk])
        detalle = self.client.get(detalle_url)
        self.assertContains(detalle, "Últimos tres precios de compra")
        self.assertContains(detalle, "Proveedor QA", count=3)
        self.assertContains(detalle, "80.00")
        self.client.force_login(self.bodeguero)
        self.assertNotContains(self.client.get(detalle_url), "Proveedor QA")

    def test_consulta_sin_compras_articulo_inexistente_y_permisos(self):
        api = reverse("api_ultimos_precios", args=[self.articulo.pk])
        pagina = reverse("ultimos_precios")
        self.assertEqual(self.client.get(api).status_code, 401)
        self.client.force_login(self.bodeguero)
        self.assertEqual(self.client.get(api).status_code, 403)
        self.assertEqual(self.client.get(pagina).status_code, 403)
        self.assertNotContains(self.client.get(reverse("catalogo_listar")), "Últimos precios")

        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(api).json(), [])
        self.assertContains(self.client.get(pagina, {"articulo": self.articulo.pk}), "aún no tiene compras")
        inexistente = self.client.get(reverse("api_ultimos_precios", args=[self.articulo.pk + 100]))
        self.assertEqual(inexistente.status_code, 404)
