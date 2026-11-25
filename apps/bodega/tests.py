from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Group
from django.contrib.staticfiles import finders
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpResponse
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse

from apps.cuentas.decorators import facultad_requerida, roles_validos
from .models import (
    Articulo,
    BitacoraAuditoria,
    Categoria,
    ConteoFisico,
    Despacho,
    DetalleConteoFisico,
    DetalleFacturaCompra,
    DevolucionProveedor,
    FacturaCompra,
    Merma,
    Obra,
    PerfilUsuario,
    Proveedor,
)
from apps.cuentas.roles import (
    ADMIN_BODEGA,
    ADMIN_TI,
    AUDITOR,
    BODEGUERO,
    FINANZAS,
    GERENTE,
    SIN_ROL,
    SUPERVISOR,
    claves_de,
    permisos_de,
    puede,
    rol_principal,
)


class RolesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.usuarios = {}
        for rol in (GERENTE, ADMIN_TI, ADMIN_BODEGA, BODEGUERO, FINANZAS, SUPERVISOR, AUDITOR):
            grupo = Group.objects.create(name=rol)
            usuario = get_user_model().objects.create_user(username=f"usuario_{grupo.pk}")
            usuario.groups.add(grupo)
            cls.usuarios[rol] = usuario

    def test_reconoce_los_siete_roles(self):
        self.assertCountEqual(roles_validos(), self.usuarios)
        for rol, usuario in self.usuarios.items():
            with self.subTest(rol=rol):
                self.assertEqual(rol_principal(usuario), rol)

    def test_solo_finanzas_emite_orden_de_compra(self):
        for rol, usuario in self.usuarios.items():
            with self.subTest(rol=rol):
                self.assertEqual(puede(usuario, "emitir_oc"), rol == FINANZAS)

    def test_solo_ti_gestiona_usuarios(self):
        for rol, usuario in self.usuarios.items():
            with self.subTest(rol=rol):
                self.assertEqual(puede(usuario, "gestionar_usuarios"), rol == ADMIN_TI)
        self.assertEqual(claves_de(self.usuarios[ADMIN_TI]), ("panel", "usuarios"))

    def test_bodeguero_puede_crear_presupuesto_desde_compras(self):
        usuario = self.usuarios[BODEGUERO]
        self.assertIn("compras", claves_de(usuario))
        self.assertTrue(permisos_de(usuario)["crear_presupuesto"])

    def test_grupo_desconocido_no_concede_facultades(self):
        usuario = get_user_model().objects.create_user(username="usuario_legado")
        usuario.groups.add(Group.objects.create(name="Admin"))
        self.assertEqual(rol_principal(usuario), SIN_ROL)
        self.assertFalse(puede(usuario, "ingresar_articulo"))

    def test_decorador_exige_autenticacion_y_facultad(self):
        @facultad_requerida("emitir_oc")
        def emitir(request):
            return HttpResponse()

        solicitud = RequestFactory().post("/compras/1/oc/")
        solicitud.user = AnonymousUser()
        self.assertEqual(emitir(solicitud).status_code, 302)

        solicitud.user = self.usuarios[BODEGUERO]
        with self.assertRaises(PermissionDenied):
            emitir(solicitud)

        solicitud.user = self.usuarios[FINANZAS]
        self.assertEqual(emitir(solicitud).status_code, 200)

    def test_formulario_actual_conserva_sus_roles_autorizados(self):
        url = reverse("articulo_crear")
        self.client.force_login(self.usuarios[ADMIN_TI])
        self.assertEqual(self.client.get(url).status_code, 403)

        self.client.force_login(self.usuarios[ADMIN_BODEGA])
        self.assertEqual(self.client.get(url).status_code, 200)

        grupo = Group.objects.create(name="Administrador General")
        usuario = get_user_model().objects.create_user(username="administrador_actual")
        usuario.groups.add(grupo)
        self.client.force_login(usuario)
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_supervisor_solo_ve_recepcion_en_bodega(self):
        self.client.force_login(self.usuarios[SUPERVISOR])
        respuesta = self.client.get(reverse("bodega_web"))
        self.assertContains(respuesta, "Recepción de despachos")
        self.assertContains(respuesta, 'class="icono-perfil"')
        self.assertNotContains(respuesta, reverse("compras"))
        self.assertNotContains(respuesta, reverse("inventario"))
        self.assertNotContains(respuesta, reverse("procesar_despacho"))
        self.assertNotContains(respuesta, 'id="modal-qr"')

    def test_rutas_anteriores_redirigen_a_inicio(self):
        inicio = reverse("inicio")
        for anterior in ("/mobile/", "/operaciones/"):
            self.assertRedirects(
                self.client.get(f"{anterior}?origen=menu"),
                f"{inicio}?origen=menu",
                fetch_redirect_response=False,
            )
        for url in (inicio, reverse("bodega_web"), reverse("inventario"), reverse("compras")):
            self.assertRedirects(self.client.get(url), f"{reverse('login')}?next={url}")

    def test_inicio_del_bodeguero_muestra_inventario_y_despachos_recientes(self):
        self.client.force_login(self.usuarios[BODEGUERO])
        bodega = reverse("bodega_web")
        categoria = Categoria.objects.create(nombre="Materiales")
        articulo = Articulo.objects.create(
            sku="INS-01", descripcion="Insumo de prueba", categoria=categoria,
            stock_fisico=2, stock_critico=2,
        )
        for numero in range(1, 7):
            obra = Obra.objects.create(nombre=f"Obra {numero}")
            Despacho.objects.create(
                articulo=articulo, obra=obra, cantidad=1, usuario=self.usuarios[BODEGUERO],
            )
        respuesta = self.client.get(reverse("inicio"))
        self.assertTemplateUsed(respuesta, "cuentas/inicio.html")
        self.assertContains(respuesta, f'href="{bodega}"')
        self.assertContains(respuesta, "Últimos despachos")
        self.assertContains(respuesta, "Obra 6")
        self.assertNotContains(respuesta, "Obra 1")
        self.assertEqual(respuesta.context["resumen_stock"], {"total": 1, "criticos": 1})
        self.assertContains(respuesta, 'class="icono-perfil"')
        self.assertNotContains(respuesta, reverse("procesar_despacho"))

        respuesta = self.client.get(bodega)
        self.assertContains(respuesta, reverse("procesar_despacho"))
        self.assertContains(respuesta, 'id="nuevo-despacho"')
        self.assertNotContains(respuesta, "Últimos despachos")

    def test_ti_no_recibe_modulos_operativos_y_finanzas_ve_su_modulo(self):
        for rol in (ADMIN_TI, FINANZAS):
            with self.subTest(rol=rol):
                self.client.force_login(self.usuarios[rol])
                respuesta = self.client.get(reverse("inicio"))
                self.assertNotContains(respuesta, f'href="{reverse("bodega_web")}"')
                self.assertNotContains(respuesta, 'href="' + reverse("articulo_crear") + '"')
                compras = reverse("compras")
                if rol == FINANZAS:
                    self.assertContains(respuesta, compras)
                    self.assertContains(self.client.get(compras), "Ingreso de Factura de Compra")
                else:
                    self.assertNotContains(respuesta, compras)
                    self.assertNotContains(respuesta, reverse("inventario"))
                    self.assertEqual(self.client.get(compras).status_code, 403)

    def test_inicio_del_supervisor_solo_ofrece_recepcion(self):
        self.client.force_login(self.usuarios[SUPERVISOR])
        respuesta = self.client.get(reverse("inicio"))
        self.assertContains(respuesta, "Recepción de despachos")
        self.assertNotContains(respuesta, "Últimos despachos")
        self.assertNotContains(respuesta, reverse("compras"))
        self.assertNotContains(respuesta, reverse("inventario"))
        self.assertEqual(self.client.get(reverse("compras")).status_code, 403)
        self.assertEqual(self.client.get(reverse("inventario")).status_code, 403)

    def test_roles_ajenos_a_bodega_no_pueden_despachar_ni_buscar_obras(self):
        for rol in (ADMIN_TI, SUPERVISOR, FINANZAS, GERENTE, AUDITOR):
            with self.subTest(rol=rol):
                self.client.force_login(self.usuarios[rol])
                self.assertEqual(self.client.post(reverse("procesar_despacho")).status_code, 403)
                self.assertEqual(self.client.get(reverse("obras_buscar"), {"q": "Edi"}).status_code, 403)

    def test_inventario_real_y_alertas_de_stock_en_bodega(self):
        self.client.force_login(self.usuarios[BODEGUERO])
        url = reverse("inventario")
        self.assertContains(self.client.get(url), "No hay insumos registrados.")

        categoria = Categoria.objects.create(nombre="Herramientas")
        for sku, stock in (("INS-BAJO", 0), ("INS-LIMITE", 2), ("INS-ALTO", 3)):
            Articulo.objects.create(
                sku=sku,
                descripcion=f"Insumo de prueba {sku}",
                categoria=categoria,
                stock_fisico=stock,
                stock_critico=2,
            )

        respuesta = self.client.get(url)
        html = respuesta.content.decode()
        for sku in ("INS-BAJO", "INS-LIMITE"):
            self.assertRegex(html, rf'<tr class="bg-red-50 text-red-800">\s*<td[^>]*>{sku}</td>')
        self.assertRegex(html, r'<tr class="text-slate-700 hover:bg-slate-50">\s*<td[^>]*>INS-ALTO</td>')
        self.assertContains(respuesta, "Stock Crítico", count=2)
        self.assertContains(respuesta, "Stock Normal", count=1)
        for sku_demo in ("FIT-098", "REF-042", "ELE-112"):
            self.assertNotContains(respuesta, sku_demo)

        Articulo.objects.filter(sku="INS-ALTO").update(stock_fisico=2)
        self.assertContains(self.client.get(url), "Stock Crítico", count=3)

    def test_bodega_puede_despachar_y_descontar_stock(self):
        self.assertIsNotNone(finders.find("js/obra_auto.js"))
        obra = Obra.objects.create(nombre="Edificio A")
        categoria = Categoria.objects.create(nombre="Herramientas")
        for rol in (BODEGUERO, ADMIN_BODEGA):
            with self.subTest(rol=rol):
                articulo = Articulo.objects.create(
                    sku=rol, descripcion="Insumo de prueba", categoria=categoria, stock_fisico=10,
                )
                usuario = self.usuarios[rol]
                self.client.force_login(usuario)
                pagina = self.client.get(reverse("bodega_web"))
                self.assertTemplateUsed(pagina, "bodega/bodega.html")
                self.assertTemplateUsed(pagina, "base.html")
                self.assertContains(pagina, 'action="' + reverse("procesar_despacho") + '"')
                self.assertContains(pagina, "js/obra_auto.js")
                self.assertContains(pagina, 'id="modal-qr"')
                self.assertNotContains(pagina, "Abrir formulario de despacho")
                self.assertEqual(self.client.get(reverse("obras_buscar"), {"q": "Ed"}).json(), [])
                self.assertEqual(
                    self.client.get(reverse("obras_buscar"), {"q": "Edi"}).json(),
                    [{"id": obra.pk, "nombre": obra.nombre}],
                )

                with TemporaryDirectory() as carpeta, patch("apps.despachos.views.Path") as ruta:
                    data_dir = Path(carpeta) / "despachos" / "data"
                    data_dir.mkdir(parents=True)
                    ruta.return_value.resolve.return_value = data_dir.parent / "views.py"
                    respuesta = self.client.post(reverse("procesar_despacho"), {
                        "articulo": articulo.pk, "obra": obra.pk, "cantidad": 3,
                    })

                self.assertRedirects(respuesta, reverse("bodega_web"))
                articulo.refresh_from_db()
                self.assertEqual(articulo.stock_fisico, 7)
                self.assertTrue(Despacho.objects.filter(articulo=articulo, usuario=usuario, cantidad=3).exists())


class HistoriasUsuarioBodegaTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.usuario = get_user_model().objects.create_user(username="bodega_hu")
        cls.usuario.groups.add(Group.objects.create(name=ADMIN_BODEGA))
        cls.categoria = Categoria.objects.create(nombre="Pruebas")
        cls.articulo = Articulo.objects.create(
            sku="HU-001",
            descripcion="Artículo HU",
            categoria=cls.categoria,
            stock_fisico=10,
        )
        cls.proveedor = Proveedor.objects.create(
            nombre="Proveedor Prueba",
            rut="76.123.456-7",
        )

    def setUp(self):
        self.client.force_login(self.usuario)

    def test_hu05_merma_registra_stock_motivo_y_bitacora(self):
        respuesta = self.client.post(reverse("merma_crear"), {
            "articulo": self.articulo.pk,
            "cantidad": 3,
            "motivo": "Producto deteriorado",
        })
        self.assertRedirects(respuesta, reverse("bodega_web"))
        self.articulo.refresh_from_db()
        self.assertEqual(self.articulo.stock_fisico, 7)
        merma = Merma.objects.get()
        self.assertEqual(merma.motivo, "Producto deteriorado")
        self.assertTrue(BitacoraAuditoria.objects.filter(
            accion="MERMA", entidad="Merma", id_registro=merma.pk
        ).exists())
        respuesta = self.client.get(reverse("cardex"))
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, "MERMA")
        self.assertContains(respuesta, "Producto deteriorado")

        respuesta = self.client.post(reverse("merma_crear"), {
            "articulo": self.articulo.pk,
            "cantidad": 8,
            "motivo": "Exceso",
        })
        self.assertContains(respuesta, "La cantidad supera el stock disponible")

    def test_hu05_rechaza_merma_con_cantidad_cero(self):
        respuesta = self.client.post(reverse("merma_crear"), {
            "articulo": self.articulo.pk,
            "cantidad": 0,
            "motivo": "Producto defectuoso",
        })
        self.assertContains(respuesta, "La cantidad debe ser mayor que cero.")
        self.articulo.refresh_from_db()
        self.assertEqual(self.articulo.stock_fisico, 10)

    def test_hu08_compra_aplica_descuentos_incrementa_stock_y_crea_factura(self):
        respuesta = self.client.post(reverse("compras"), {
            "numero_factura": "FAC-HU08",
            "proveedor": self.proveedor.pk,
            "articulo": self.articulo.pk,
            "cantidad": 2,
            "precio_unitario": "100",
            "descuento_1": "10",
            "descuento_2": "5",
            "descuento_3": "2",
        })
        self.assertRedirects(respuesta, reverse("compras"))
        self.articulo.refresh_from_db()
        self.assertEqual(self.articulo.stock_fisico, 12)
        factura = FacturaCompra.objects.get(numero_factura="FAC-HU08")
        detalle = DetalleFacturaCompra.objects.get(factura=factura)
        self.assertEqual(str(detalle.subtotal_neto), "167.58")
        self.assertTrue(BitacoraAuditoria.objects.filter(
            accion="COMPRA", entidad="FacturaCompra", id_registro=factura.pk
        ).exists())

    def test_hu08_descuentos_vacios_se_guardan_como_cero(self):
        respuesta = self.client.post(reverse("compras"), {
            "numero_factura": "FAC-DESC-CERO",
            "proveedor": self.proveedor.pk,
            "articulo": self.articulo.pk,
            "cantidad": 1,
            "precio_unitario": "100",
            "descuento_1": "",
            "descuento_2": "",
            "descuento_3": "",
        })
        self.assertRedirects(respuesta, reverse("compras"))
        detalle = DetalleFacturaCompra.objects.get(
            factura__numero_factura="FAC-DESC-CERO"
        )
        self.assertEqual(detalle.descuento_1, 0)
        self.assertEqual(detalle.descuento_2, 0)
        self.assertEqual(detalle.descuento_3, 0)

    def test_hu010_devolucion_descuenta_stock_y_registra_movimiento(self):
        respuesta = self.client.post(reverse("devolucion_crear"), {
            "proveedor": self.proveedor.pk,
            "articulo": self.articulo.pk,
            "cantidad": 4,
            "motivo": "Producto rechazado",
        })
        self.assertRedirects(respuesta, reverse("devolucion_crear"))
        self.articulo.refresh_from_db()
        self.assertEqual(self.articulo.stock_fisico, 6)
        devolucion = DevolucionProveedor.objects.get()
        self.assertTrue(BitacoraAuditoria.objects.filter(
            accion="DEVOLUCION_PROVEEDOR",
            entidad="DevolucionProveedor",
            id_registro=devolucion.pk,
        ).exists())

    def test_hu010_rechaza_devolucion_con_cantidad_cero(self):
        respuesta = self.client.post(reverse("devolucion_crear"), {
            "proveedor": self.proveedor.pk,
            "articulo": self.articulo.pk,
            "cantidad": 0,
            "motivo": "Cambio",
        })
        self.assertContains(respuesta, "La cantidad debe ser mayor que cero.")
        self.articulo.refresh_from_db()
        self.assertEqual(self.articulo.stock_fisico, 10)

    def test_hu09_conteo_con_y_sin_discrepancias(self):
        respuesta = self.client.post(reverse("conteo_crear"), {
            f"stock_contado_{self.articulo.pk}": "8",
            "observacion": "Reconteo",
        })
        self.assertRedirects(respuesta, reverse("conteo_discrepancias"))
        conteo = ConteoFisico.objects.get()
        detalle = DetalleConteoFisico.objects.get(conteo=conteo)
        self.assertEqual(detalle.diferencia, -2)
        respuesta = self.client.get(reverse("conteo_discrepancias"))
        self.assertContains(respuesta, "Diferencia")
        self.assertContains(respuesta, "-2")

        self.client.post(reverse("conteo_crear"), {
            f"stock_contado_{self.articulo.pk}": "10",
            "observacion": "Sin diferencias",
        })
        respuesta = self.client.get(reverse("conteo_discrepancias"))
        self.assertContains(
            respuesta,
            "No se encontraron discrepancias en el inventario",
        )

    def test_hu09_rechaza_valores_negativos_o_no_numericos(self):
        respuesta = self.client.post(reverse("conteo_crear"), {
            f"stock_contado_{self.articulo.pk}": "-2",
        })
        self.assertContains(respuesta, "no puede ser negativo")
        self.assertFalse(ConteoFisico.objects.exists())

        respuesta = self.client.post(reverse("conteo_crear"), {
            f"stock_contado_{self.articulo.pk}": "texto",
        })
        self.assertContains(respuesta, "debe ser un número entero")
        self.assertFalse(ConteoFisico.objects.exists())


class AccesoRUTTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.usuario = get_user_model().objects.create_user(
            username="cuenta_bodega", first_name="Camila", last_name="Fuentes", password="clave-de-test"
        )
        cls.usuario.groups.add(Group.objects.create(name=ADMIN_BODEGA))
        PerfilUsuario.objects.create(usuario=cls.usuario, rut="11.111.111-1")

    def test_login_por_rut_muestra_nombre_y_rol_en_web(self):
        respuesta = self.client.post(reverse("login"), {
            "rut": "11.111.111-1", "password": "clave-de-test",
        }, follow=True)
        self.assertRedirects(respuesta, reverse("inicio"))
        self.assertContains(respuesta, "Camila Fuentes")
        self.assertContains(respuesta, ADMIN_BODEGA)
        self.assertNotContains(respuesta, "Usuario de Sistema")
        self.assertEqual(self.client.get(reverse("articulo_crear")).status_code, 200)

    def test_login_por_rut_sin_puntos_ni_guion_abre_inicio(self):
        for rol in (BODEGUERO, SUPERVISOR, FINANZAS):
            with self.subTest(rol=rol):
                self.usuario.groups.set([Group.objects.create(name=rol)])
                respuesta = self.client.post(reverse("login"), {
                    "rut": "111111111", "password": "clave-de-test",
                }, follow=True)
                self.assertRedirects(respuesta, reverse("inicio"))
                self.assertContains(respuesta, "Camila Fuentes")
                self.assertContains(respuesta, rol)
                self.client.logout()

    def test_login_no_pide_elegir_version(self):
        respuesta = self.client.get(reverse("login"))
        self.assertContains(respuesta, 'name="rut"')
        self.assertNotContains(respuesta, 'name="destino"')

    def test_login_respeta_el_destino_original_del_enlace(self):
        url = reverse("articulo_crear")
        respuesta = self.client.post(reverse("login"), {
            "rut": "11.111.111-1", "password": "clave-de-test", "next": url,
        })
        self.assertRedirects(respuesta, url)

    def test_acceso_por_rut_respeta_los_permisos_de_ti(self):
        usuario = get_user_model().objects.create_user(username="cuenta_ti", password="clave-de-test")
        usuario.groups.add(Group.objects.create(name=ADMIN_TI))
        PerfilUsuario.objects.create(usuario=usuario, rut="22.222.222-2")

        respuesta = self.client.post(reverse("login"), {
            "rut": "22.222.222-2", "password": "clave-de-test",
        }, follow=True)
        self.assertRedirects(respuesta, reverse("inicio"))
        self.assertContains(respuesta, "cuenta_ti")
        self.assertContains(respuesta, ADMIN_TI)
        self.assertEqual(self.client.get(reverse("articulo_crear")).status_code, 403)

    def test_login_rechaza_nombre_de_usuario_rut_incorrecto_y_clave_erronea(self):
        for rut, password in (
            ("cuenta_bodega", "clave-de-test"),
            ("11.111.111-2", "clave-de-test"),
            ("22.222.222-2", "clave-de-test"),
            ("11.111.111-1", "clave-incorrecta"),
        ):
            with self.subTest(rut=rut):
                respuesta = self.client.post(reverse("login"), {"rut": rut, "password": password})
                self.assertContains(respuesta, "RUT o contraseña incorrectos")
                self.assertNotIn("_auth_user_id", self.client.session)

    def test_login_rechaza_cuentas_inactivas(self):
        self.usuario.is_active = False
        self.usuario.save(update_fields=["is_active"])
        respuesta = self.client.post(reverse("login"), {
            "rut": "11.111.111-1", "password": "clave-de-test",
        })
        self.assertContains(respuesta, "RUT o contraseña incorrectos")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_bloqueo_por_rut_normalizado_vence_sin_prorroga_por_intentos(self):
        url = reverse("login")
        inicio = datetime(2026, 11, 19, 15, tzinfo=timezone.utc)
        perfil = self.usuario.perfilusuario

        with patch("apps.cuentas.forms.timezone.now", return_value=inicio) as reloj:
            for rut in ("11.111.111-1", "11111111-1", "111111111", "11.111.111-1", "11111111-1"):
                respuesta = self.client.post(url, {"rut": rut, "password": "clave-incorrecta"})
                self.assertContains(respuesta, "RUT o contraseña incorrectos, o cuenta inactiva.")

            perfil.refresh_from_db()
            vence = inicio + timedelta(minutes=15)
            self.assertEqual(perfil.intentos_fallidos, 5)
            self.assertEqual(perfil.bloqueado_hasta, vence)

            otro_cliente = Client()
            credenciales = {"rut": "11.111.111-1", "password": "clave-de-test"}
            reloj.return_value = vence - timedelta(seconds=1)
            respuesta = otro_cliente.post(url, credenciales)
            self.assertContains(respuesta, "RUT o contraseña incorrectos, o cuenta inactiva.")
            self.assertNotIn("_auth_user_id", otro_cliente.session)
            perfil.refresh_from_db()
            self.assertEqual(perfil.bloqueado_hasta, vence)

            reloj.return_value = vence
            self.assertRedirects(otro_cliente.post(url, credenciales), reverse("inicio"))

        perfil.refresh_from_db()
        self.assertEqual(perfil.intentos_fallidos, 0)
        self.assertIsNone(perfil.bloqueado_hasta)

    def test_login_exitoso_antes_del_limite_reinicia_los_intentos(self):
        url = reverse("login")
        rut = "11.111.111-1"
        for _ in range(4):
            self.client.post(url, {"rut": rut, "password": "clave-incorrecta"})

        self.assertRedirects(
            self.client.post(url, {"rut": rut, "password": "clave-de-test"}),
            reverse("inicio"),
        )
        perfil = self.usuario.perfilusuario
        self.assertEqual(perfil.intentos_fallidos, 0)
        self.assertIsNone(perfil.bloqueado_hasta)

    def test_rut_es_unico_independientemente_del_formato(self):
        otro = get_user_model().objects.create_user(username="otro_usuario")
        duplicado = PerfilUsuario(usuario=otro, rut="11111111-1")
        with self.assertRaisesMessage(ValidationError, "RUT"):
            duplicado.full_clean()
