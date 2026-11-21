import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import IntegrityError
from django.test import TestCase
from django.urls import reverse

from apps.bodega.models import Articulo, BitacoraAuditoria, Categoria, MovimientoCardex, Obra
from apps.cuentas.roles import ADMIN_BODEGA, BODEGUERO, SUPERVISOR
from .models import Despacho


class DespachoTransaccionalTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.usuario = get_user_model().objects.create_user(username="bodega")
        cls.usuario.groups.add(Group.objects.create(name=BODEGUERO))
        cls.obra = Obra.objects.create(nombre="Obra de prueba")
        categoria = Categoria.objects.create(nombre="Refrigeración")
        cls.articulo = Articulo.objects.create(
            sku="REF-001", descripcion="Filtro", categoria=categoria, stock_fisico=10,
        )

    def setUp(self):
        self.client.force_login(self.usuario)

    def despachar(self, cantidad):
        return self.client.post(reverse("procesar_despacho"), {
            "articulo": self.articulo.pk, "obra": self.obra.pk, "cantidad": cantidad,
        })

    def test_despacho_guarda_cardex_bitacora_y_respalda_despues_del_commit(self):
        with TemporaryDirectory() as carpeta:
            base = Path(carpeta)
            datos = base / "data"
            datos.mkdir()
            respaldo = datos / "mock_mongodb_cardex.json"
            respaldo.write_text('[{"_id": "registro anterior"}]', encoding="utf-8")
            with patch("apps.bodega.respaldo.ARCHIVO_RESPALDO", respaldo):
                with self.captureOnCommitCallbacks(execute=True) as callbacks:
                    respuesta = self.despachar(3)
                    self.assertEqual(len(json.loads(respaldo.read_text(encoding="utf-8"))), 1)
                guardado = json.loads(respaldo.read_text(encoding="utf-8"))

        self.assertRedirects(respuesta, reverse("despachos"))
        self.assertEqual(len(callbacks), 1)
        self.articulo.refresh_from_db()
        self.assertEqual(self.articulo.stock_fisico, 7)
        despacho = Despacho.objects.get()
        self.assertEqual(despacho.tipo_movimiento, "DESPACHO")
        movimiento = MovimientoCardex.objects.get()
        self.assertEqual(movimiento.tipo, MovimientoCardex.Tipo.DESPACHO)
        self.assertEqual(movimiento.articulo_id, self.articulo.pk)
        self.assertEqual(movimiento.obra_id, self.obra.pk)
        self.assertEqual(movimiento.usuario_id, self.usuario.pk)
        self.assertEqual(movimiento.cantidad, 3)
        self.assertEqual(movimiento.stock_resultante, 7)
        evento = BitacoraAuditoria.objects.get(entidad="MovimientoCardex", id_registro=movimiento.pk)
        self.assertEqual(evento.valor_anterior, {"stock_fisico": 10})
        self.assertEqual(evento.valor_nuevo["stock_fisico"], 7)
        self.assertEqual(evento.valor_nuevo["despacho_id"], despacho.pk)
        self.assertEqual(guardado[0]["_id"], "registro anterior")
        self.assertEqual(guardado[1]["movimiento_id"], movimiento.pk)
        self.assertEqual(guardado[1]["_id"], despacho.mongodb_id)

        pagina = self.client.get(reverse("despachos"))
        self.assertTemplateUsed(pagina, "despachos/despacho.html")
        self.assertContains(pagina, f"Despacho #{despacho.pk}")
        self.assertContains(pagina, "REF-001 · Filtro")
        self.assertContains(pagina, "Obra de prueba")
        self.assertContains(pagina, "3 unidad")

    def test_lista_solo_muestra_despachos_autorizados(self):
        campos = {"articulo": self.articulo, "obra": self.obra, "cantidad": 1}
        propio = Despacho.objects.create(usuario=self.usuario, **campos)
        admin = get_user_model().objects.create_user(username="admin-bodega")
        admin.groups.add(Group.objects.create(name=ADMIN_BODEGA))
        ajeno = Despacho.objects.create(usuario=admin, **campos)
        Despacho.objects.create(usuario=admin, tipo_movimiento="MERMA", **campos)

        pagina = self.client.get(reverse("despachos"))
        self.assertEqual(list(pagina.context["despachos_recientes"]), [propio])

        self.client.force_login(admin)
        pagina = self.client.get(reverse("despachos"))
        self.assertEqual(list(pagina.context["despachos_recientes"]), [ajeno, propio])

        supervisor = get_user_model().objects.create_user(username="supervisor")
        supervisor.groups.add(Group.objects.create(name=SUPERVISOR))
        self.client.force_login(supervisor)
        pagina = self.client.get(reverse("despachos"))
        self.assertNotContains(pagina, "Despachos recientes")

    def test_bodega_y_url_anterior_llevan_a_despachos(self):
        pagina = self.client.get(reverse("bodega_web"))
        self.assertContains(pagina, f'href="{reverse("despachos")}"')
        self.assertRedirects(self.client.get(reverse("bodega_mobile")), reverse("despachos"))

    def test_stock_insuficiente_no_genera_cambios(self):
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            respuesta = self.despachar(11)

        self.assertRedirects(respuesta, reverse("despachos"))
        self.assertEqual(callbacks, [])
        self.comprobar_reversion()

    def test_si_falla_bitacora_se_revierte_el_despacho(self):
        with (
            patch("apps.despachos.views.BitacoraAuditoria.objects.create", side_effect=IntegrityError),
            self.assertLogs("apps.despachos.views", level="ERROR"),
            self.captureOnCommitCallbacks(execute=True) as callbacks,
        ):
            respuesta = self.despachar(3)

        self.assertRedirects(respuesta, reverse("despachos"))
        self.assertEqual(callbacks, [])
        self.comprobar_reversion()

    def test_si_falla_el_respaldo_el_despacho_permanece_confirmado(self):
        with TemporaryDirectory() as carpeta:
            base = Path(carpeta)
            datos = base / "data"
            datos.mkdir()
            with (
                patch("apps.bodega.respaldo.ARCHIVO_RESPALDO", datos / "mock_mongodb_cardex.json"),
                patch("apps.bodega.respaldo.os.replace", side_effect=OSError),
                self.assertLogs("apps.bodega.respaldo", level="ERROR") as logs,
                self.captureOnCommitCallbacks(execute=True),
            ):
                respuesta = self.despachar(3)
            self.assertEqual(list(datos.iterdir()), [])

        self.assertRedirects(respuesta, reverse("despachos"))
        self.articulo.refresh_from_db()
        self.assertEqual(self.articulo.stock_fisico, 7)
        self.assertEqual(Despacho.objects.count(), 1)
        self.assertEqual(MovimientoCardex.objects.count(), 1)
        self.assertEqual(BitacoraAuditoria.objects.count(), 1)
        self.assertIn("Falló el respaldo simulado", logs.output[0])

    def comprobar_reversion(self):
        self.articulo.refresh_from_db()
        self.assertEqual(self.articulo.stock_fisico, 10)
        self.assertFalse(Despacho.objects.exists())
        self.assertFalse(MovimientoCardex.objects.exists())
        self.assertFalse(BitacoraAuditoria.objects.exists())
