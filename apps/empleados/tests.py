from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from apps.bodega.models import PerfilUsuario
from apps.cuentas.roles import ADMIN_TI, FINANZAS, GERENTE, RRHH

from .models import Empleado, Vacacion


class RecursosHumanosTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.usuarios = {}
        for indice, rol in enumerate((RRHH, GERENTE, ADMIN_TI, FINANZAS)):
            usuario = get_user_model().objects.create_user(username=f"cuenta_{indice}", password="clave-de-prueba")
            usuario.groups.add(Group.objects.create(name=rol))
            cls.usuarios[rol] = usuario
        PerfilUsuario.objects.create(usuario=cls.usuarios[RRHH], rut="11.111.118-9")

    def setUp(self):
        self.datos = {
            "rut": "11.111.111-1", "nombres": "Andrea", "apellidos": "Pruebas", "cargo": "Técnico",
            "correo": "empleado@example.com", "fecha_ingreso": "2026-11-01",
            "tipo_contrato": "Plazo fijo", "fecha_termino_contrato": "2027-01-01", "activo": "on",
        }

    def test_acceso_por_rut_y_gestion_de_ficha_con_vacaciones(self):
        listado = reverse("empleados:empleados")
        self.assertRedirects(
            self.client.post(reverse("login"), {"rut": "11.111.118-9", "password": "clave-de-prueba"}),
            reverse("inicio"),
        )
        pagina = self.client.get(listado)
        self.assertContains(pagina, "<h1>Empleados</h1>", html=True)
        self.assertEqual(pagina.context["modulo_actual"]["nombre"], "Empleados")
        respuesta = self.client.post(reverse("empleados:crear"), self.datos)
        empleado = Empleado.objects.get(rut="11111111-1")
        detalle = reverse("empleados:detalle", args=[empleado.pk])
        self.assertRedirects(respuesta, detalle)
        self.assertContains(self.client.get(listado, {"q": "11.111.111-1"}), "Andrea Pruebas")
        self.assertContains(self.client.get(listado, {"q": "Andrea Pruebas"}), "Andrea Pruebas")
        self.assertRedirects(self.client.post(reverse("empleados:editar", args=[empleado.pk]), {
            **self.datos, "cargo": "Supervisora", "activo": "",
        }), detalle)
        empleado.refresh_from_db()
        self.assertFalse(empleado.activo)
        self.assertEqual(empleado.cargo, "Supervisora")
        vacaciones = reverse("empleados:crear_vacacion", args=[empleado.pk])
        self.assertRedirects(self.client.post(vacaciones, {"desde": "2026-12-01", "hasta": "2026-12-04"}), detalle)
        periodo = empleado.vacaciones.get()
        editar_periodo = reverse("empleados:editar_vacacion", args=[empleado.pk, periodo.pk])
        self.assertRedirects(self.client.post(editar_periodo, {"desde": "2026-12-01", "hasta": "2026-12-05"}), detalle)
        self.assertContains(self.client.get(detalle), "05/12/2026")
        self.assertEqual(get_user_model().objects.count(), 4)

    def test_permisos_de_consulta_y_edicion(self):
        empleado = Empleado.objects.create(
            rut="11111111-1", nombres="Persona", apellidos="Prueba", cargo="Técnico", fecha_ingreso="2026-11-01",
        )
        detalle = reverse("empleados:detalle", args=[empleado.pk])
        editar = reverse("empleados:editar", args=[empleado.pk])
        self.assertEqual(self.client.get(detalle).status_code, 302)
        self.client.force_login(self.usuarios[GERENTE])
        respuesta = self.client.get(detalle)
        self.assertContains(respuesta, "Persona Prueba")
        self.assertContains(respuesta, editar)
        self.assertRedirects(self.client.post(editar, self.datos), detalle)
        self.assertRedirects(
            self.client.post(reverse("empleados:crear_vacacion", args=[empleado.pk]), {
                "desde": "2026-12-01", "hasta": "2026-12-04",
            }),
            detalle,
        )
        for rol in (ADMIN_TI, FINANZAS):
            with self.subTest(rol=rol):
                self.client.force_login(self.usuarios[rol])
                self.assertEqual(self.client.get(detalle).status_code, 403)
                self.assertEqual(self.client.post(reverse("empleados:crear"), self.datos).status_code, 403)

    def test_rechaza_rut_duplicado_y_fechas_invertidas(self):
        self.client.force_login(self.usuarios[RRHH])
        nueva = reverse("empleados:crear")
        respuesta = self.client.post(nueva, {**self.datos, "fecha_termino_contrato": "2026-10-01"})
        self.assertContains(respuesta, "El término debe ser igual o posterior")
        self.assertFalse(Empleado.objects.exists())
        self.client.post(nueva, self.datos)
        duplicado = self.client.post(nueva, {**self.datos, "rut": "11111111-1"})
        self.assertEqual(duplicado.status_code, 200)
        empleado = Empleado.objects.get()
        vacaciones = reverse("empleados:crear_vacacion", args=[empleado.pk])
        respuesta = self.client.post(vacaciones, {"desde": "2026-12-03", "hasta": "2026-12-01"})
        self.assertContains(respuesta, "La fecha de término debe ser igual o posterior")
        self.assertFalse(Vacacion.objects.exists())
