from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from apps.cuentas.roles import AUDITOR, BODEGUERO
from .models import Articulo, BitacoraAuditoria, Categoria, ConteoInventario, DetalleConteoInventario, MovimientoCardex


class ConteosInventarioTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Usuario = get_user_model()
        cls.bodeguero = Usuario.objects.create_user(username="bodeguero")
        cls.bodeguero.groups.add(Group.objects.create(name=BODEGUERO))
        cls.auditor = Usuario.objects.create_user(username="auditor")
        cls.auditor.groups.add(Group.objects.create(name=AUDITOR))
        categoria = Categoria.objects.create(nombre="Insumos")
        cls.a = Articulo.objects.create(sku="A", descripcion="Filtro", categoria=categoria, stock_fisico=10)
        cls.b = Articulo.objects.create(sku="B", descripcion="Válvula", categoria=categoria, stock_fisico=5)
        cls.c = Articulo.objects.create(sku="C", descripcion="Cable", categoria=categoria, stock_fisico=4)

    def setUp(self):
        self.client.force_login(self.bodeguero)

    def _post_api(self, *lineas):
        return self.client.post(
            reverse("registrar_conteo"),
            {"detalles": [
                {"articulo_id": articulo, "stock_fisico": cantidad, "stock_sistema": 100}
                for articulo, cantidad in lineas
            ]},
            content_type="application/json",
        )

    def test_api_conserva_el_saldo_historico_y_filtra_discrepancias(self):
        respuesta = self._post_api((self.a.pk, 0), (self.b.pk, 5), (self.c.pk, 7))
        self.assertEqual(respuesta.status_code, 201)
        datos = respuesta.json()
        self.assertEqual(datos["usuario_id"], self.bodeguero.pk)
        self.assertEqual([d["stock_sistema"] for d in datos["detalles"]], [10, 5, 4])
        self.assertEqual([d["diferencia"] for d in datos["detalles"]], [-10, 0, 3])
        self.assertEqual(Articulo.objects.get(pk=self.a.pk).stock_fisico, 10)
        self.assertFalse(MovimientoCardex.objects.exists())
        evento = BitacoraAuditoria.objects.get(entidad="ConteoInventario", id_registro=datos["id"])
        self.assertEqual(evento.valor_nuevo, {"articulos": 3, "discrepancias": 2})

        Articulo.objects.filter(pk=self.a.pk).update(stock_fisico=30)
        self.client.force_login(self.auditor)
        url = reverse("api_discrepancias_conteo", args=[datos["id"]])
        self.assertEqual(self.client.get(url).json(), [datos["detalles"][0], datos["detalles"][2]])
        reporte = self.client.get(reverse("discrepancias_conteo", args=[datos["id"]]))
        self.assertContains(reporte, "Discrepancias")
        self.assertContains(reporte, "<td>-10</td>", html=True)
        self.assertContains(reporte, "<td>+3</td>", html=True)
        self.assertContains(reporte, reverse("conteos"))
        historial = self.client.get(reverse("conteos"))
        self.assertEqual(historial.context["pagina"].object_list[0].discrepancias, 2)
        self.assertContains(self.client.get(reverse("auditoria")), "Ver reporte del conteo")

    def test_formulario_permite_registrar_y_consultar_un_conteo_sin_diferencias(self):
        url = reverse("nuevo_conteo")
        self.assertContains(self.client.get(reverse("bodega_web")), reverse("conteos"))
        self.assertContains(self.client.get(url), "Registrar conteo físico")
        respuesta = self.client.post(url, {
            "form-TOTAL_FORMS": 2, "form-INITIAL_FORMS": 0,
            "form-0-articulo": self.a.pk, "form-0-stock_fisico": 10,
            "form-1-articulo": self.b.pk, "form-1-stock_fisico": 5,
        })
        conteo = ConteoInventario.objects.get()
        reporte_url = reverse("discrepancias_conteo", args=[conteo.pk])
        self.assertRedirects(respuesta, reporte_url)
        self.assertContains(self.client.get(reporte_url), "No hay diferencias de stock.")
        self.assertEqual(self.client.get(reverse("api_discrepancias_conteo", args=[conteo.pk])).json(), [])

    def test_datos_invalidos_no_guardan_conteos_parciales(self):
        for lineas in (
            (), ((self.a.pk, -1),), ((self.a.pk, True),), ((self.a.pk, 2**31),),
            ((self.a.pk, 10), (self.a.pk, 10)), ((self.a.pk, 10), (self.c.pk + 100, 2)),
        ):
            with self.subTest(lineas=lineas):
                self.assertEqual(self._post_api(*lineas).status_code, 400)
        self.assertFalse(ConteoInventario.objects.exists())
        self.assertFalse(BitacoraAuditoria.objects.exists())

    def test_auditor_puede_consultar_pero_no_registrar(self):
        self.client.force_login(self.auditor)
        self.assertEqual(self.client.get(reverse("conteos")).status_code, 200)
        self.assertEqual(self.client.get(reverse("nuevo_conteo")).status_code, 403)
        self.assertEqual(self._post_api((self.a.pk, 0)).status_code, 403)

    def test_fallo_de_auditoria_revierte_el_conteo_y_sus_detalles(self):
        with (
            patch("apps.bodega.conteos.BitacoraAuditoria.objects.create", side_effect=IntegrityError),
            self.assertLogs("apps.bodega.conteos", level="ERROR"),
        ):
            self.assertEqual(self._post_api((self.a.pk, 0)).status_code, 500)
        self.assertFalse(ConteoInventario.objects.exists())
        self.assertFalse(DetalleConteoInventario.objects.exists())

    def test_base_de_datos_rechaza_articulos_repetidos_y_stock_negativo(self):
        conteo = ConteoInventario.objects.create(usuario=self.bodeguero)
        DetalleConteoInventario.objects.create(conteo=conteo, articulo=self.a, stock_sistema=10, stock_fisico=9)
        for articulo, stock in ((self.a, 8), (self.b, -1)):
            with self.subTest(articulo=articulo), self.assertRaises(IntegrityError), transaction.atomic():
                DetalleConteoInventario.objects.create(
                    conteo=conteo, articulo=articulo, stock_sistema=10, stock_fisico=stock,
                )
