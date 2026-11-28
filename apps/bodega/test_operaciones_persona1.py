from datetime import date
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import Client, TestCase
from django.urls import reverse

from apps.compras.models import DetalleFactura, EntidadComercial, FacturaCompra
from apps.cuentas.roles import ADMIN_BODEGA, AUDITOR, BODEGUERO
from apps.despachos.models import Despacho
from apps.despachos.preparacion import ObraForm
from apps.despachos.views import guardar_despacho
from .conteos import guardar_conteo
from .models import (
    Articulo, BitacoraAuditoria, Categoria, ConteoInventario, MovimientoCardex, Obra, OperacionSincronizada,
)


class OperacionesBodegaTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        categoria = Categoria.objects.create(nombre="Insumos")
        cls.original = Articulo.objects.create(sku="A", descripcion="Filtro", categoria=categoria, stock_fisico=10)
        cls.reemplazo = Articulo.objects.create(sku="B", descripcion="Válvula", categoria=categoria, stock_fisico=5)
        cls.obra = Obra.objects.create(nombre="Obra Norte")
        cls.proveedor = EntidadComercial.objects.create(rut="11111111-1", razon_social="Proveedor")
        cls.bodeguero = cls.usuario_con_rol("bodega", BODEGUERO)
        cls.admin = cls.usuario_con_rol("admin-bodega", ADMIN_BODEGA)
        cls.auditor = cls.usuario_con_rol("consulta", AUDITOR)

    @staticmethod
    def usuario_con_rol(username, rol):
        usuario = get_user_model().objects.create_user(username=username)
        usuario.groups.add(Group.objects.get_or_create(name=rol)[0])
        return usuario

    def setUp(self):
        self.client.force_login(self.bodeguero)

    def costo_compra(self, articulo, costo, *, estado=FacturaCompra.EstadoIngreso.CONFIRMADO, fecha=date(2026, 1, 1)):
        factura = FacturaCompra.objects.create(
            proveedor=self.proveedor, estado_ingreso=estado, folio=str(uuid4()), fecha_emision=fecha,
            subtotal_neto=costo, iva=0, total=costo, usuario_registro=self.admin,
        )
        DetalleFactura.objects.create(
            factura=factura, articulo=articulo, cantidad=1, precio_unitario_neto=costo, costo_unitario_neto_final=costo,
        )

    def despacho(self, cantidad=3):
        return guardar_despacho(self.bodeguero, {"obra": self.obra.pk, "articulo": self.original.pk, "cantidad": cantidad})

    def saldos(self):
        return tuple(Articulo.objects.filter(pk__in=[self.original.pk, self.reemplazo.pk]).order_by("pk").values_list(
            "stock_fisico", flat=True,
        ))


class ObraTests(OperacionesBodegaTests):
    def test_ficha_edita_fecha_y_protege_costo_calculado(self):
        self.assertEqual(self.obra.estado, Obra.Estado.EN_PROCESO)
        self.assertEqual(len(Obra.TipoServicio.choices), 8)
        self.assertNotIn("costo_materiales", ObraForm().fields)
        url = reverse("obras_editar", args=[self.obra.pk])
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.admin)
        respuesta = self.client.post(url, {
            "nombre": self.obra.nombre, "fecha_creacion": "2026-03-17", "tipo_servicio": "ELECTRICO",
            "estado": "SUSPENDIDA", "supervisor_responsable": "Valeria Soto", "presupuesto_asignado": "1000",
            "costo_materiales": "9999",
        })
        self.assertRedirects(respuesta, reverse("obras"))
        self.obra.refresh_from_db()
        self.assertEqual(self.obra.fecha_creacion, date(2026, 3, 17))
        self.assertEqual(self.obra.get_tipo_servicio_display(), "Proyecto eléctrico")
        self.assertEqual(self.obra.supervisor_responsable, "Valeria Soto")
        self.assertEqual(self.obra.costo_materiales, 0)
        self.assertContains(self.client.get(url), 'value="2026-03-17"')
        self.assertRaises(ValidationError, self.despacho)

    def test_despachos_acumulan_costo_neto_confirmado_y_lo_congelan(self):
        self.costo_compra(self.original, Decimal("12.50"))
        primero = self.despacho()
        self.costo_compra(self.original, Decimal("20.75"), fecha=date(2026, 2, 1))
        self.costo_compra(self.original, 55, fecha=date(2026, 3, 1), estado=FacturaCompra.EstadoIngreso.PENDIENTE)
        segundo = self.despacho(2)
        self.obra.refresh_from_db()
        self.assertEqual(primero.costo_unitario, Decimal("12.50"))
        self.assertEqual(segundo.costo_unitario, Decimal("20.75"))
        self.assertEqual(self.obra.costo_materiales, Decimal("79.00"))
        self.assertEqual(self.saldos(), (5, 5))


class SalidasBodegaTests(OperacionesBodegaTests):
    def test_uso_interno_persiste_responsable_destino_y_observaciones(self):
        url = reverse("uso_interno_crear")
        self.assertContains(self.client.get(url), "Solicitante responsable")
        datos = {"articulo_id": self.original.pk, "cantidad": 2, "destino": "Taller", "solicitante_responsable": "Rita"}
        with patch("apps.bodega.egresos.guardar_respaldo_cardex") as respaldar:
            with self.captureOnCommitCallbacks(execute=True):
                self.assertRedirects(self.client.post(url, {**datos, "justificacion": "Prueba de equipos"}), reverse("bodega_web"))
        movimiento = MovimientoCardex.objects.get()
        evento = BitacoraAuditoria.objects.get()
        self.assertEqual((movimiento.tipo, movimiento.variacion_stock, movimiento.stock_resultante), ("USO_INTERNO", -2, 8))
        self.assertEqual((movimiento.destino, movimiento.solicitante_responsable, movimiento.justificacion), ("Taller", "Rita", "Prueba de equipos"))
        self.assertEqual(evento.valor_nuevo["destino"], "Taller")
        self.assertEqual(respaldar.call_args.args[0][0]["solicitante_responsable"], "Rita")
        api = reverse("registrar_uso_interno")
        self.assertEqual(self.client.post(api, {**datos, "destino": " "}, content_type="application/json").status_code, 400)
        self.client.force_login(self.auditor)
        self.assertEqual(self.client.post(api, datos, content_type="application/json").status_code, 403)
        self.assertEqual(MovimientoCardex.objects.count(), 1)

    def datos_cambio(self, despacho, **cambios):
        return {
            "despacho_id": despacho.pk, "articulo_id": self.reemplazo.pk, "cantidad": 2,
            "cantidad_reemplazo": 1, "justificacion": "Corregir el repuesto entregado", **cambios,
        }

    def test_cambio_enlaza_las_dos_partidas_y_valora_el_reemplazo(self):
        self.costo_compra(self.original, 10)
        self.costo_compra(self.reemplazo, 25)
        original = self.despacho()
        url = reverse("cambio_crear")
        self.assertContains(self.client.get(url), self.obra.nombre)
        self.assertRedirects(self.client.post(url, self.datos_cambio(original)), reverse("bodega_web"))
        entrada, salida = MovimientoCardex.objects.filter(tipo="CAMBIO").order_by("pk")
        self.assertEqual((entrada.variacion_stock, salida.variacion_stock), (2, -1))
        self.assertEqual(entrada.despacho_origen, original)
        self.assertEqual(salida.movimiento_original, entrada)
        self.assertEqual(salida.obra, self.obra)
        self.assertEqual(Despacho.objects.get(tipo_movimiento="CAMBIO").mongodb_id, str(salida.pk))
        self.assertEqual(BitacoraAuditoria.objects.filter(valor_nuevo__cambio_id=entrada.pk).count(), 2)
        self.assertEqual(self.saldos(), (9, 4))
        self.obra.refresh_from_db()
        self.assertEqual(self.obra.costo_materiales, 35)
        self.assertContains(self.client.post(url, self.datos_cambio(original)), "supera lo que queda en obra")
        self.assertEqual(MovimientoCardex.objects.filter(tipo="CAMBIO").count(), 2)

    def test_fallo_en_segunda_partida_revierte_el_cambio_completo(self):
        despacho = self.despacho()
        registrar = BitacoraAuditoria.objects.create

        def registrar_primera_partida(**datos):
            if datos["valor_nuevo"]["variacion_stock"] < 0:
                raise IntegrityError
            return registrar(**datos)

        with (
            patch("apps.bodega.egresos.BitacoraAuditoria.objects.create", side_effect=registrar_primera_partida),
            self.assertLogs("apps.bodega.egresos", level="ERROR"),
        ):
            respuesta = self.client.post(reverse("cambio_crear"), self.datos_cambio(despacho))
        self.assertContains(respuesta, "No se pudo registrar el cambio")
        self.assertEqual(self.saldos(), (7, 5))
        self.assertEqual(MovimientoCardex.objects.count(), 1)
        self.assertEqual(BitacoraAuditoria.objects.count(), 1)
        self.assertEqual(Despacho.objects.count(), 1)


class CierreConteoTests(OperacionesBodegaTests):
    def test_finaliza_y_aplica_diferencias_una_sola_vez(self):
        conteo, _ = guardar_conteo(self.bodeguero, [(self.original.pk, 8), (self.reemplazo.pk, 9)], "Recuento")
        detalle = reverse("discrepancias_conteo", args=[conteo.pk])
        finalizar = reverse("finalizar_conteo", args=[conteo.pk])
        self.assertContains(self.client.get(detalle), "Finalizar y ajustar stock")
        self.assertEqual(self.client.get(finalizar).status_code, 405)
        self.assertRedirects(self.client.post(finalizar), detalle)
        self.assertRedirects(self.client.post(finalizar), detalle)
        conteo.refresh_from_db()
        self.assertEqual(conteo.estado, ConteoInventario.Estado.FINALIZADO)
        self.assertEqual(self.saldos(), (8, 9))
        movimientos = MovimientoCardex.objects.filter(tipo="AJUSTE", conteo_origen=conteo).order_by("pk")
        self.assertEqual(list(movimientos.values_list("cantidad", "variacion_stock")), [(2, -2), (4, 4)])
        self.assertEqual(BitacoraAuditoria.objects.filter(accion="FINALIZAR").count(), 1)

    def test_conteo_desactualizado_y_rol_sin_permiso(self):
        conteo, _ = guardar_conteo(self.bodeguero, [(self.original.pk, 8)], "")
        url = reverse("finalizar_conteo", args=[conteo.pk])
        self.client.force_login(self.auditor)
        self.assertEqual(self.client.post(url).status_code, 403)
        self.client.force_login(self.bodeguero)
        self.despacho()
        respuesta = self.client.post(url, follow=True)
        self.assertContains(respuesta, "El inventario cambió")
        conteo.refresh_from_db()
        self.assertEqual(conteo.estado, ConteoInventario.Estado.EN_PROCESO)
        self.assertEqual(self.saldos(), (7, 5))
        self.assertFalse(MovimientoCardex.objects.filter(tipo="AJUSTE").exists())

    def test_fallo_de_auditoria_revierte_estado_y_stock(self):
        conteo, _ = guardar_conteo(self.bodeguero, [(self.original.pk, 3)], "")
        with (
            patch("apps.bodega.conteos.BitacoraAuditoria.objects.create", side_effect=IntegrityError),
            self.assertLogs("apps.bodega.conteos", level="ERROR"),
        ):
            self.client.post(reverse("finalizar_conteo", args=[conteo.pk]))
        conteo.refresh_from_db()
        self.assertEqual(conteo.estado, ConteoInventario.Estado.EN_PROCESO)
        self.assertEqual(self.saldos(), (10, 5))
        self.assertFalse(MovimientoCardex.objects.exists())


class SincronizacionTests(OperacionesBodegaTests):
    def setUp(self):
        super().setUp()
        self.url = reverse("sincronizar_offline")

    def operacion(self, tipo="despacho", datos=None):
        return {
            "usuario": str(self.bodeguero.pk), "clave": str(uuid4()), "tipo": tipo,
            "datos": datos if datos is not None else {"obra": self.obra.pk, "articulo": self.original.pk, "cantidad": 3},
        }

    def post(self, operacion):
        return self.client.post(self.url, operacion, content_type="application/json")

    def test_reintento_no_duplica_despacho_ni_respaldo(self):
        operacion = self.operacion()
        with (
            patch("apps.bodega.offline.guardar_respaldo_sincronizacion") as respaldar,
            patch("apps.despachos.views.guardar_respaldo_cardex"),
            self.captureOnCommitCallbacks(execute=True),
        ):
            primera = self.post(operacion)
            repetida = self.post(operacion)
        self.assertEqual(primera.status_code, 201)
        self.assertEqual(repetida.json(), {**primera.json(), "repetida": True})
        self.assertEqual(Despacho.objects.count(), 1)
        self.assertEqual(self.saldos(), (7, 5))
        self.assertEqual(respaldar.call_args.args[0]["operacion_id"], operacion["clave"])
        respaldar.assert_called_once()
        self.assertEqual(self.post({**operacion, "datos": {**operacion["datos"], "cantidad": 4}}).status_code, 409)

    def test_conteo_requiere_base_actual_y_se_guarda_una_vez(self):
        articulo = self.client.get(self.url).json()["articulos"][0]
        detalle = {"articulo_id": articulo["id"], "stock_fisico": 8, "stock_sistema": articulo["stock_fisico"], "ultimo_movimiento": articulo["ultimo_movimiento"]}
        operacion = self.operacion("conteo", {"detalles": [detalle]})
        primera = self.post(operacion)
        self.assertEqual(primera.status_code, 201)
        self.assertEqual(self.post(operacion).json()["id"], primera.json()["id"])
        self.despacho()
        self.assertEqual(self.post(self.operacion("conteo", {"detalles": [detalle]})).status_code, 409)
        self.assertEqual(ConteoInventario.objects.count(), 1)
        self.assertEqual(OperacionSincronizada.objects.count(), 1)
        self.assertEqual(self.saldos(), (7, 5))

    def test_valida_sesion_rol_y_csrf(self):
        operacion = self.operacion()
        self.assertEqual(Client().post(self.url, operacion, content_type="application/json").status_code, 401)
        self.assertEqual(self.post({**operacion, "usuario": str(self.admin.pk)}).status_code, 403)
        self.client.force_login(self.auditor)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.post({**operacion, "usuario": str(self.auditor.pk)}).status_code, 403)
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.bodeguero)
        catalogo = csrf.get(self.url)
        self.assertIn("no-store", catalogo.headers["Cache-Control"])
        self.assertEqual(csrf.post(self.url, operacion, content_type="application/json").status_code, 403)
        respuesta = csrf.post(self.url, operacion, content_type="application/json", HTTP_X_CSRFTOKEN=catalogo.json()["csrf"])
        self.assertEqual(respuesta.status_code, 201)

    def test_stock_insuficiente_no_consume_clave_de_reintento(self):
        operacion = self.operacion(datos={"obra": self.obra.pk, "articulo": self.original.pk, "cantidad": 11})
        self.assertEqual(self.post(operacion).status_code, 409)
        self.assertFalse(OperacionSincronizada.objects.exists())
        operacion["datos"]["cantidad"] = 1
        self.assertEqual(self.post(operacion).status_code, 201)

    def test_pantalla_publica_y_worker_sin_datos_privados(self):
        pantalla = Client().get(reverse("operacion_offline"))
        self.assertContains(pantalla, "Nuevo conteo físico")
        self.assertNotContains(pantalla, self.obra.nombre)
        worker = Client().get(reverse("service_worker"))
        self.assertEqual(worker.status_code, 200)
        self.assertIn("text/javascript", worker.headers["Content-Type"])
        worker.close()
