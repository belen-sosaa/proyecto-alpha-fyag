from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from apps.compras.models import EntidadComercial
from apps.cuentas.roles import ADMIN_BODEGA, BODEGUERO
from .models import Articulo, BitacoraAuditoria, Categoria, MovimientoCardex


class EgresosTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        usuario = get_user_model()
        cls.bodeguero = usuario.objects.create_user(username="bodega")
        cls.bodeguero.groups.add(Group.objects.create(name=BODEGUERO))
        cls.admin = usuario.objects.create_user(username="admin-bodega")
        cls.admin.groups.add(Group.objects.create(name=ADMIN_BODEGA))
        categoria = Categoria.objects.create(nombre="Insumos")
        cls.articulo = Articulo.objects.create(sku="QA", descripcion="Filtro", categoria=categoria, stock_fisico=10)
        cls.proveedor = EntidadComercial.objects.create(rut="99999998-0", razon_social="Proveedor QA")
        cls.cliente = EntidadComercial.objects.create(rut="99999999-9", razon_social="Cliente QA", tipo_entidad="CLIENTE")

    def post_api(self, nombre, **campos):
        return self.client.post(reverse(nombre), {
            "articulo_id": self.articulo.pk, "cantidad": 2, "justificacion": "Daño del embalaje",
            **campos,
        }, content_type="application/json")

    def test_merma_desde_bodega_y_respaldo_posterior_a_la_transaccion(self):
        self.client.force_login(self.bodeguero)
        self.assertContains(self.client.get(reverse("bodega_web")), reverse("merma_crear"))
        url = reverse("merma_crear")
        self.assertContains(self.client.get(url), "Registrar merma")
        with patch("apps.bodega.egresos.guardar_respaldo_cardex") as guardar:
            with self.captureOnCommitCallbacks(execute=True) as callbacks:
                respuesta = self.client.post(url, {
                    "articulo_id": self.articulo.pk, "cantidad": 2, "justificacion": "  Embalaje dañado  ",
                })
                guardar.assert_not_called()
            guardar.assert_called_once()
        self.assertRedirects(respuesta, reverse("bodega_web"))
        self.assertEqual(len(callbacks), 1)
        movimiento = MovimientoCardex.objects.get()
        self.assertEqual(movimiento.tipo, MovimientoCardex.Tipo.MERMA)
        self.assertEqual(movimiento.justificacion, "Embalaje dañado")
        self.assertEqual(guardar.call_args.args[0][0]["movimiento_id"], movimiento.pk)
        self.assertEqual(Articulo.objects.get(pk=self.articulo.pk).stock_fisico, 8)
        evento = BitacoraAuditoria.objects.get(entidad="MovimientoCardex", id_registro=movimiento.pk)
        self.assertEqual(evento.valor_anterior["stock_fisico"], 10)
        self.assertEqual(evento.valor_nuevo["stock_fisico"], 8)

    def test_merma_invalida_o_sin_stock_no_registra_cambios(self):
        self.client.force_login(self.bodeguero)
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            for motivo in ("", " \t\n", None):
                respuesta = self.post_api("registrar_merma", justificacion=motivo)
                self.assertEqual(respuesta.status_code, 400)
            self.assertEqual(self.post_api("registrar_merma", cantidad=11).status_code, 409)
        self.assertEqual(callbacks, [])
        self.assertFalse(MovimientoCardex.objects.exists())
        self.assertFalse(BitacoraAuditoria.objects.exists())
        self.assertEqual(Articulo.objects.get(pk=self.articulo.pk).stock_fisico, 10)

    def test_el_modelo_y_la_base_rechazan_merma_sin_motivo(self):
        for motivo in ("", "  \n\t", None):
            movimiento = MovimientoCardex(
                tipo=MovimientoCardex.Tipo.MERMA, articulo=self.articulo, usuario=self.bodeguero,
                cantidad=3, stock_resultante=7, justificacion=motivo,
            )
            with self.subTest(motivo=motivo), self.assertRaises(ValidationError):
                movimiento.full_clean()
            with self.assertRaises(IntegrityError), transaction.atomic():
                movimiento.save()

    def test_devolucion_registra_proveedor_documento_y_stock_sin_permitir_edicion(self):
        self.client.force_login(self.bodeguero)
        self.assertEqual(self.post_api("registrar_devolucion", proveedor_id=self.proveedor.pk).status_code, 403)
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse("bodega_web")), reverse("devolucion_crear"))
        self.assertEqual(self.post_api("registrar_devolucion", proveedor_id=self.cliente.pk).status_code, 400)
        self.assertEqual(self.post_api("registrar_devolucion").status_code, 400)

        respuesta = self.post_api("registrar_devolucion", proveedor_id=self.proveedor.pk, documento_referencia="Guía 100")
        self.assertEqual(respuesta.status_code, 201)
        movimiento = MovimientoCardex.objects.get()
        self.assertEqual(movimiento.tipo, MovimientoCardex.Tipo.DEVOLUCION)
        self.assertEqual(movimiento.proveedor, self.proveedor)
        self.assertEqual(movimiento.documento_referencia, "Guía 100")
        self.assertEqual(Articulo.objects.get(pk=self.articulo.pk).stock_fisico, 8)
        self.assertEqual(BitacoraAuditoria.objects.get().valor_nuevo["proveedor_id"], self.proveedor.pk)
        with self.assertRaises(IntegrityError), transaction.atomic():
            MovimientoCardex.objects.filter(pk=movimiento.pk).update(cantidad=1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            MovimientoCardex.objects.filter(pk=movimiento.pk).delete()

    def test_fallo_de_bitacora_revierte_devolucion_y_no_respalda(self):
        self.client.force_login(self.admin)
        with (
            patch("apps.bodega.egresos.BitacoraAuditoria.objects.create", side_effect=IntegrityError),
            self.assertLogs("apps.bodega.egresos", level="ERROR"),
            self.captureOnCommitCallbacks(execute=True) as callbacks,
        ):
            respuesta = self.post_api("registrar_devolucion", proveedor_id=self.proveedor.pk)
        self.assertEqual(respuesta.status_code, 500)
        self.assertEqual(callbacks, [])
        self.assertFalse(MovimientoCardex.objects.exists())
        self.assertFalse(BitacoraAuditoria.objects.exists())
        self.assertEqual(Articulo.objects.get(pk=self.articulo.pk).stock_fisico, 10)
