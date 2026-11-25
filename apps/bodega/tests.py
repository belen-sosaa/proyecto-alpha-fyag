from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Group
from django.contrib.staticfiles import finders
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpResponse
from django.test import RequestFactory, TestCase
from django.urls import reverse

from apps.compras.models import EntidadComercial, FacturaCompra
from apps.cuentas.decorators import facultad_requerida, roles_validos
from apps.despachos.models import Despacho
from .models import Articulo, BitacoraAuditoria, Categoria, Obra, PerfilUsuario
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

    def test_supervisor_solo_ve_recepcion_en_bodega_y_despachos(self):
        self.client.force_login(self.usuarios[SUPERVISOR])
        for ruta in ("bodega_web", "despachos"):
            with self.subTest(ruta=ruta):
                respuesta = self.client.get(reverse(ruta))
                self.assertContains(respuesta, "Recepción de despachos")
                self.assertNotContains(respuesta, f'href="{reverse("finanzas")}"')
                self.assertNotContains(respuesta, f'href="{reverse("catalogo_listar")}"')
                self.assertNotContains(respuesta, reverse("procesar_despacho"))

    def test_ti_no_recibe_modulos_operativos_y_finanzas_ve_su_modulo(self):
        for rol in (ADMIN_TI, FINANZAS):
            with self.subTest(rol=rol):
                self.client.force_login(self.usuarios[rol])
                respuesta = self.client.get(reverse("bodega_web"))
                self.assertNotContains(respuesta, 'id="existencias"')
                self.assertNotContains(respuesta, 'href="' + reverse("articulo_crear") + '"')
                if rol == FINANZAS:
                    self.assertContains(respuesta, f'href="{reverse("finanzas")}"')
                    self.assertContains(respuesta, f'href="{reverse("compras")}"')
                else:
                    self.assertNotContains(respuesta, f'href="{reverse("finanzas")}"')
                    self.assertNotContains(respuesta, f'href="{reverse("catalogo_listar")}"')

    def test_modulos_operativos_separan_inventario_auditoria_y_facturas(self):
        usuario = self.usuarios[ADMIN_BODEGA]
        self.client.force_login(usuario)
        proveedor = EntidadComercial.objects.create(rut="11111111-1", razon_social="Proveedor de prueba")
        FacturaCompra.objects.create(
            proveedor=proveedor, folio="QA-01", fecha_emision="2026-11-21", usuario_registro=usuario,
            subtotal_neto=100, iva=19, total=119,
        )
        BitacoraAuditoria.objects.create(
            usuario=usuario, accion="CREAR", entidad="Articulo", id_registro=1, detalle="Evento de prueba",
        )

        bodega = self.client.get(reverse("bodega_web"))
        for ruta in ("bodega_web", "despachos", "catalogo_listar", "auditoria", "compras", "finanzas"):
            self.assertContains(bodega, f'href="{reverse(ruta)}"')
        self.assertNotContains(bodega, "Evento de prueba")
        self.assertContains(self.client.get(reverse("auditoria")), "Evento de prueba")
        self.assertContains(self.client.get(reverse("compras")), "Factura QA-01")
        self.assertContains(self.client.get(reverse("finanzas")), "119.00")

        self.client.force_login(self.usuarios[BODEGUERO])
        self.assertEqual(self.client.get(reverse("auditoria")).status_code, 403)
        self.assertEqual(self.client.get(reverse("finanzas")).status_code, 403)
        compras = self.client.get(reverse("compras"))
        self.assertContains(compras, "Factura QA-01")
        self.assertNotContains(compras, "119.00")

    def test_roles_ajenos_a_bodega_no_pueden_despachar_ni_buscar_obras(self):
        for rol in (ADMIN_TI, SUPERVISOR, FINANZAS, GERENTE, AUDITOR):
            with self.subTest(rol=rol):
                self.client.force_login(self.usuarios[rol])
                self.assertEqual(self.client.post(reverse("procesar_despacho")).status_code, 403)
                self.assertEqual(self.client.get(reverse("obras_buscar"), {"q": "Edi"}).status_code, 403)

    def test_cardex_muestra_valores_de_auditoria_y_protege_costos(self):
        BitacoraAuditoria.objects.create(
            usuario=self.usuarios[ADMIN_BODEGA], accion="EDITAR", entidad="Articulo", id_registro=1,
            valor_anterior={"stock_fisico": 10}, valor_nuevo={"stock_fisico": 8},
        )
        BitacoraAuditoria.objects.create(
            usuario=self.usuarios[FINANZAS], accion="COMPRA", entidad="FacturaCompra", id_registro=1,
            valor_nuevo={"subtotal_neto": "999.12"},
        )
        url = reverse("cardex")
        self.client.force_login(self.usuarios[AUDITOR])
        pagina = self.client.get(url)
        self.assertContains(pagina, "Anterior")
        self.assertContains(pagina, "stock_fisico")
        self.assertNotContains(pagina, "999.12")
        self.client.force_login(self.usuarios[FINANZAS])
        self.assertContains(self.client.get(url), "999.12")
        self.client.force_login(self.usuarios[BODEGUERO])
        self.assertEqual(self.client.get(url).status_code, 403)

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
                pagina = self.client.get(reverse("despachos"))
                self.assertContains(pagina, 'action="' + reverse("procesar_despacho") + '"')
                self.assertContains(pagina, "js/obra_auto.js")
                self.assertEqual(self.client.get(reverse("obras_buscar"), {"q": "Ed"}).json(), [])
                self.assertEqual(
                    self.client.get(reverse("obras_buscar"), {"q": "Edi"}).json(),
                    [{"id": obra.pk, "nombre": obra.nombre}],
                )

                respuesta = self.client.post(reverse("procesar_despacho"), {
                    "articulo": articulo.pk, "obra": obra.pk, "cantidad": 3,
                })

                self.assertRedirects(respuesta, reverse("despachos"))
                articulo.refresh_from_db()
                self.assertEqual(articulo.stock_fisico, 7)
                self.assertTrue(Despacho.objects.filter(articulo=articulo, usuario=usuario, cantidad=3).exists())


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
        self.assertRedirects(respuesta, reverse("bodega_web"))
        self.assertContains(respuesta, "Camila Fuentes")
        self.assertContains(respuesta, ADMIN_BODEGA)
        self.assertNotContains(respuesta, "Usuario de Sistema")
        self.assertEqual(self.client.get(reverse("articulo_crear")).status_code, 200)

    def test_login_por_rut_sin_puntos_abre_despachos_segun_el_rol(self):
        for rol in (BODEGUERO, SUPERVISOR):
            with self.subTest(rol=rol):
                self.usuario.groups.set([Group.objects.create(name=rol)])
                respuesta = self.client.post(reverse("login"), {
                    "rut": "11111111-1", "password": "clave-de-test",
                }, follow=True)
                self.assertRedirects(respuesta, reverse("despachos"))
                self.assertContains(respuesta, "Camila Fuentes")
                self.assertContains(respuesta, rol)
                self.client.logout()

    def test_login_no_pide_elegir_version(self):
        respuesta = self.client.get(reverse("login"))
        self.assertContains(respuesta, 'name="rut"')
        self.assertContains(respuesta, "js/formato_rut.js")
        self.assertIsNotNone(finders.find("js/formato_rut.js"))
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
        self.assertRedirects(respuesta, reverse("bodega_web"))
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

    def test_rut_es_unico_independientemente_del_formato(self):
        otro = get_user_model().objects.create_user(username="otro_usuario")
        duplicado = PerfilUsuario(usuario=otro, rut="11111111-1")
        with self.assertRaisesMessage(ValidationError, "RUT"):
            duplicado.full_clean()

    def test_formatos_del_rut_comparten_el_bloqueo_y_su_vencimiento(self):
        url = reverse("login")
        inicio = datetime(2026, 11, 19, 15, tzinfo=timezone.utc)
        perfil = self.usuario.perfilusuario
        with patch("apps.cuentas.forms.timezone.now", return_value=inicio) as reloj:
            for rut in ("11.111.111-1", "11111111-1", "111111111", "11.111.111-1", "11111111-1"):
                self.client.post(url, {"rut": rut, "password": "clave-incorrecta"})

            perfil.refresh_from_db()
            vence = inicio + timedelta(minutes=15)
            self.assertEqual(perfil.intentos_fallidos, 5)
            self.assertEqual(perfil.bloqueado_hasta, vence)

            credenciales = {"rut": "111111111", "password": "clave-de-test"}
            reloj.return_value = vence - timedelta(seconds=1)
            self.assertContains(self.client.post(url, credenciales), "RUT o contraseña incorrectos")
            perfil.refresh_from_db()
            self.assertEqual(perfil.bloqueado_hasta, vence)

            reloj.return_value = vence
            self.assertRedirects(self.client.post(url, credenciales), reverse("bodega_web"))

        perfil.refresh_from_db()
        self.assertEqual(perfil.intentos_fallidos, 0)
        self.assertIsNone(perfil.bloqueado_hasta)
