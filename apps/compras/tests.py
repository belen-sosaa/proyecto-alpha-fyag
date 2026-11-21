from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import IntegrityError
from django.test import TestCase
from django.urls import reverse

from apps.bodega.models import Articulo, BitacoraAuditoria, Categoria, MovimientoCardex
from apps.cuentas.roles import ADMIN_BODEGA, BODEGUERO, FINANZAS
from .models import EntidadComercial, FacturaCompra


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
        with patch("apps.compras.views.guardar_respaldo_simulado") as respaldo:
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
