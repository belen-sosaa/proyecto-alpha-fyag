import json
from contextlib import closing
from io import BytesIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import IntegrityError
from django.test import TestCase
from django.urls import reverse
from pymongo.errors import ServerSelectionTimeoutError
from pypdf import PdfReader

from apps.bodega.models import Articulo, BitacoraAuditoria, Categoria, MovimientoCardex, Obra
from apps.cuentas.roles import ADMIN_BODEGA, BODEGUERO, SUPERVISOR
from .models import Despacho, RecepcionDespacho


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
        with patch("apps.despachos.views.guardar_respaldo_cardex") as guardar:
            with self.captureOnCommitCallbacks(execute=True) as callbacks:
                respuesta = self.despachar(3)
                guardar.assert_not_called()
            guardar.assert_called_once()
        documento, = guardar.call_args.args[0]

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
        self.assertEqual(documento["movimiento_id"], movimiento.pk)
        self.assertEqual(documento["despacho_id"], despacho.pk)
        self.assertEqual(documento["_id"], despacho.mongodb_id)
        self.assertEqual(documento["stock_resultante"], 7)

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
        with (
            patch("apps.bodega.respaldo.obtener_base", side_effect=ServerSelectionTimeoutError),
            self.assertLogs("apps.bodega.respaldo", level="ERROR") as logs,
            self.captureOnCommitCallbacks(execute=True),
        ):
            respuesta = self.despachar(3)

        self.assertRedirects(respuesta, reverse("despachos"))
        self.articulo.refresh_from_db()
        self.assertEqual(self.articulo.stock_fisico, 7)
        self.assertEqual(Despacho.objects.count(), 1)
        self.assertEqual(MovimientoCardex.objects.count(), 1)
        self.assertEqual(BitacoraAuditoria.objects.count(), 1)
        self.assertIn("Falló el respaldo de Cardex en MongoDB", logs.output[0])

    def comprobar_reversion(self):
        self.articulo.refresh_from_db()
        self.assertEqual(self.articulo.stock_fisico, 10)
        self.assertFalse(Despacho.objects.exists())
        self.assertFalse(MovimientoCardex.objects.exists())
        self.assertFalse(BitacoraAuditoria.objects.exists())


class RecepcionDespachoTests(TestCase):
    firma = [[[28, 80], [45, 52], [70, 55]], [[95, 100], [180, 100]]]

    @classmethod
    def setUpTestData(cls):
        usuario = get_user_model()
        grupo_supervisor = Group.objects.create(name=SUPERVISOR)
        cls.supervisor = usuario.objects.create_user(username="supervision", first_name="Ana")
        cls.supervisor.groups.add(grupo_supervisor)
        cls.otro_supervisor = usuario.objects.create_user(username="otra-supervision")
        cls.otro_supervisor.groups.add(grupo_supervisor)
        cls.bodeguero = usuario.objects.create_user(username="bodeguero")
        cls.bodeguero.groups.add(Group.objects.create(name=BODEGUERO))
        cls.otro_bodeguero = usuario.objects.create_user(username="otro-bodeguero")
        cls.otro_bodeguero.groups.add(Group.objects.get(name=BODEGUERO))
        cls.admin = usuario.objects.create_user(username="admin-bodega")
        cls.admin.groups.add(Group.objects.create(name=ADMIN_BODEGA))
        categoria = Categoria.objects.create(nombre="Insumos")
        cls.articulo = Articulo.objects.create(
            sku="REF-002", descripcion="Válvula", categoria=categoria, stock_fisico=10,
        )
        obra = Obra.objects.create(nombre="Obra Norte")
        cls.despacho = Despacho.objects.create(
            articulo=cls.articulo, obra=obra, usuario=cls.bodeguero, cantidad=2,
        )
        cls.comprobante_url = reverse("comprobante_despacho", args=[cls.despacho.pk])
        cls.pdf_url = reverse("descargar_comprobante_despacho", args=[cls.despacho.pk])
        cls.recepcion_url = reverse("registrar_recepcion", args=[cls.despacho.pk])

    def test_firma_vinculada_al_comprobante_y_auditada_sin_movimiento_de_stock(self):
        self.client.force_login(self.supervisor)
        self.assertContains(self.client.get(reverse("despachos")), "Revisar y firmar")
        self.assertContains(self.client.get(self.comprobante_url), "<canvas")

        respuesta = self.client.post(self.recepcion_url, {
            "firma": json.dumps(self.firma), "receptor_id": self.otro_supervisor.pk,
        })

        self.assertRedirects(respuesta, self.comprobante_url)
        recepcion = RecepcionDespacho.objects.get(despacho=self.despacho)
        self.assertEqual(recepcion.receptor, self.supervisor)
        self.assertEqual(recepcion.nombre_receptor, "Ana")
        self.assertEqual(recepcion.firma, self.firma)
        self.assertIsNotNone(recepcion.fecha_recepcion)
        evento = BitacoraAuditoria.objects.get(entidad="RecepcionDespacho", id_registro=recepcion.pk)
        self.assertEqual(evento.valor_nuevo, {
            "despacho_id": self.despacho.pk, "receptor_id": self.supervisor.pk,
        })
        self.articulo.refresh_from_db()
        self.assertEqual(self.articulo.stock_fisico, 10)
        self.assertFalse(MovimientoCardex.objects.exists())

        for usuario in (self.supervisor, self.bodeguero, self.admin):
            with self.subTest(usuario=usuario):
                self.client.force_login(usuario)
                pagina = self.client.get(self.comprobante_url)
                self.assertTemplateUsed(pagina, "despachos/despacho.html")
                self.assertContains(pagina, "Ana")
                self.assertContains(pagina, 'points="28,80 45,52 70,55"')
                self.assertNotContains(pagina, "<canvas")

        for usuario in (self.otro_supervisor, self.otro_bodeguero):
            with self.subTest(usuario=usuario):
                self.client.force_login(usuario)
                self.assertEqual(self.client.get(self.comprobante_url).status_code, 404)

    def test_validacion_y_permisos_de_firma(self):
        self.client.force_login(self.bodeguero)
        self.assertEqual(self.client.post(self.recepcion_url, {"firma": json.dumps(self.firma)}).status_code, 403)
        self.client.force_login(self.supervisor)
        self.assertEqual(self.client.get(self.recepcion_url).status_code, 405)

        for firma in ("", "[]", "[[[0,0],[0,0]]]", "[[[0,0],[601,100]]]", "[[[false,0],[12,10]]]", "[[[0,0]],null]"):
            with self.subTest(firma=firma):
                respuesta = self.client.post(self.recepcion_url, {"firma": firma})
                self.assertRedirects(respuesta, self.comprobante_url)
                self.assertFalse(RecepcionDespacho.objects.exists())
        self.assertFalse(BitacoraAuditoria.objects.exists())

    def test_pdf_descargable_con_firma_y_permisos_del_comprobante(self):
        self.assertRedirects(self.client.get(self.pdf_url), f"{reverse('login')}?next={self.pdf_url}")
        self.client.force_login(self.supervisor)
        self.assertNotContains(self.client.get(self.comprobante_url), self.pdf_url)
        self.assertEqual(self.client.get(self.pdf_url).status_code, 404)
        self.client.post(self.recepcion_url, {"firma": json.dumps(self.firma)})

        for usuario in (self.supervisor, self.bodeguero, self.admin):
            with self.subTest(usuario=usuario):
                self.client.force_login(usuario)
                self.assertContains(self.client.get(self.comprobante_url), self.pdf_url)
                with closing(self.client.get(self.pdf_url)) as respuesta:
                    self.assertEqual(respuesta.status_code, 200)
                    self.assertEqual(respuesta["Content-Type"], "application/pdf")
                    self.assertIn(f'attachment; filename="despacho-{self.despacho.pk}-firmado.pdf"',
                                  respuesta["Content-Disposition"])
                    self.assertIn("no-store", respuesta["Cache-Control"])
                    pdf = PdfReader(BytesIO(b"".join(respuesta.streaming_content)))
                self.assertEqual(len(pdf.pages), 1)
                pagina = pdf.pages[0]
                texto = pagina.extract_text()
                for dato in ("Obra Norte", "REF-002", "Válvula", "2 unidad", "bodeguero", "Ana", "Firma de recepción"):
                    self.assertIn(dato, texto)
                self.assertTrue(any(
                    operador == b"l" and tuple(punto) == (29.25, 109.2)
                    for punto, operador in pagina.get_contents().operations
                ))

        for usuario in (self.otro_supervisor, self.otro_bodeguero):
            with self.subTest(usuario=usuario):
                self.client.force_login(usuario)
                self.assertEqual(self.client.get(self.pdf_url).status_code, 404)

    def test_segundo_envio_no_reemplaza_la_firma(self):
        self.client.force_login(self.supervisor)
        self.client.post(self.recepcion_url, {"firma": json.dumps(self.firma)})

        self.client.force_login(self.otro_supervisor)
        respuesta = self.client.post(self.recepcion_url, {"firma": "[[[0,0],[40,20]]]"})

        self.assertRedirects(respuesta, reverse("despachos"))
        recepcion = RecepcionDespacho.objects.get()
        self.assertEqual(recepcion.receptor, self.supervisor)
        self.assertEqual(recepcion.firma, self.firma)
        self.assertEqual(BitacoraAuditoria.objects.count(), 1)

    def test_fallo_de_auditoria_revierte_la_recepcion(self):
        self.client.force_login(self.supervisor)
        with (
            patch("apps.despachos.views.BitacoraAuditoria.objects.create", side_effect=IntegrityError),
            self.assertLogs("apps.despachos.views", level="ERROR"),
        ):
            respuesta = self.client.post(self.recepcion_url, {"firma": json.dumps(self.firma)})

        self.assertRedirects(respuesta, self.comprobante_url)
        self.assertFalse(RecepcionDespacho.objects.exists())


class ArticuloPorCodigoTests(TestCase):
    # Se prueba el contrato JSON y los permisos de la consulta que usa el lector.
    @classmethod
    def setUpTestData(cls):
        usuarios = get_user_model()
        cls.bodeguero = usuarios.objects.create_user(username="cuenta_bodega")
        cls.bodeguero.groups.add(Group.objects.create(name=BODEGUERO))
        cls.supervisor = usuarios.objects.create_user(username="cuenta_supervisor")
        cls.supervisor.groups.add(Group.objects.create(name=SUPERVISOR))
        cls.articulo = Articulo.objects.create(
            sku="ENS-002",
            codigo_barras="0012345678905",
            descripcion="Insumo identificado",
            categoria=Categoria.objects.create(nombre="Herramientas"),
        )

    def test_busqueda_exacta_conserva_los_ceros_del_codigo(self):
        self.client.force_login(self.bodeguero)
        url = reverse("articulo_por_codigo")
        resultado = self.client.get(url, {"codigo": " 0012345678905 "})
        self.assertEqual(resultado.status_code, 200)
        self.assertEqual(resultado.json(), {
            "id": self.articulo.pk,
            "sku": "ENS-002",
            "codigo_barras": "0012345678905",
            "descripcion": "Insumo identificado",
            "unidad_medida": "unidad",
        })
        self.assertEqual(self.client.get(url, {"codigo": "12345678905"}).status_code, 404)
        self.assertEqual(self.client.get(url, {"codigo": "  "}).status_code, 400)
        self.assertEqual(self.client.get(url, {"codigo": "1" * 101}).status_code, 400)
        self.assertEqual(self.client.post(url).status_code, 405)

    def test_consulta_requiere_sesion_y_permiso_de_despacho(self):
        url = reverse("articulo_por_codigo")
        self.assertRedirects(self.client.get(url), f"{reverse('login')}?next={url}")
        self.client.force_login(self.supervisor)
        self.assertEqual(self.client.get(url, {"codigo": self.articulo.codigo_barras}).status_code, 403)

    def test_lector_se_muestra_en_nuevo_despacho_segun_el_rol(self):
        url = reverse("despachos")
        self.assertEqual(reverse("despachos_web"), url)
        self.client.force_login(self.bodeguero)
        pagina = self.client.get(url)
        self.assertContains(pagina, 'id="lector-codigo"')
        self.assertContains(pagina, f'data-url="{reverse("articulo_por_codigo")}"')
        self.assertContains(pagina, 'id="menu-operativo"')
        self.client.force_login(self.supervisor)
        self.assertNotContains(self.client.get(url), 'id="lector-codigo"')

    def test_escaneo_busca_el_codigo_equivalente_solo_si_es_inequivoco(self):
        self.client.force_login(self.bodeguero)
        url = reverse("articulo_por_codigo")
        consulta = {"codigo": "012345678905", "formato": "UPC_A"}
        respuesta = self.client.get(url, consulta)
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.json()["id"], self.articulo.pk)
        self.assertEqual(self.client.get(url, {"codigo": consulta["codigo"]}).status_code, 404)

        Articulo.objects.create(
            sku="ENS-003", codigo_barras=consulta["codigo"],
            descripcion="Otro registro", categoria=self.articulo.categoria,
        )
        self.assertEqual(self.client.get(url, consulta).status_code, 409)
