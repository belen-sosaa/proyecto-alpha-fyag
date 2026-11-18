from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Group
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpResponse
from django.test import RequestFactory, TestCase
from django.urls import reverse

from .decorators import facultad_requerida, roles_validos
from .models import PerfilUsuario
from .roles import (
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
            "rut": "11.111.111-1", "password": "clave-de-test", "destino": "web",
        }, follow=True)
        self.assertRedirects(respuesta, reverse("bodega_web"))
        self.assertContains(respuesta, "Camila Fuentes")
        self.assertContains(respuesta, ADMIN_BODEGA)
        self.assertNotContains(respuesta, "Usuario de Sistema")
        self.assertEqual(self.client.get(reverse("articulo_crear")).status_code, 200)

    def test_login_por_rut_sin_puntos_muestra_rol_en_mobile(self):
        respuesta = self.client.post(reverse("login"), {
            "rut": "11111111-1", "password": "clave-de-test", "destino": "mobile",
        }, follow=True)
        self.assertRedirects(respuesta, reverse("bodega_mobile"))
        self.assertContains(respuesta, "Camila Fuentes")
        self.assertContains(respuesta, ADMIN_BODEGA)

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
