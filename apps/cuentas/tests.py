import re

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from apps.bodega.models import PerfilUsuario


class AccesoCsrfTests(TestCase):
    def test_formulario_desactualizado_permite_reintentar_con_un_token_valido(self):
        usuario = get_user_model().objects.create_user(username="seguridad_login", password="clave-de-prueba")
        perfil = PerfilUsuario.objects.create(usuario=usuario, rut="11.111.111-1")
        cliente = Client(enforce_csrf_checks=True)
        login = reverse("login")
        formulario = cliente.get(login)
        token = re.search(rb'name="csrfmiddlewaretoken" value="([^"]+)"', formulario.content)[1].decode()
        credenciales = {"rut": perfil.rut, "password": "clave-de-prueba", "csrfmiddlewaretoken": token}
        self.assertRedirects(cliente.post(login, credenciales), reverse("inicio"))

        rechazo = cliente.post(login, credenciales)
        self.assertContains(rechazo, "El formulario de acceso se actualizó.", status_code=403)
        token_nuevo = re.search(rb'name="csrfmiddlewaretoken" value="([^"]+)"', rechazo.content)[1].decode()
        self.assertRedirects(cliente.post(login, {**credenciales, "csrfmiddlewaretoken": token_nuevo}), reverse("inicio"))


class SalidaTests(TestCase):
    # Se activa la validación CSRF para comprobar que solo la confirmación puede cerrar la sesión.
    def test_salida_pide_confirmacion_y_cierra_sesion_por_post_con_csrf(self):
        usuario = get_user_model().objects.create_user(username="cuenta_de_prueba")
        cliente = Client(enforce_csrf_checks=True)
        cliente.force_login(usuario)
        inicio = reverse("inicio")
        salida = reverse("logout")

        panel = cliente.get(inicio)
        self.assertContains(panel, "js/navegacion_operativa.js")
        self.assertContains(panel, '<dialog id="confirmacion-salida"')
        self.assertContains(panel, 'data-confirmar-salida aria-haspopup="dialog"')
        self.assertContains(panel, '<button type="button" data-cancelar-salida autofocus>Cancelar</button>', html=True)
        self.assertContains(panel, f'<form method="post" action="{salida}">')
        self.assertContains(panel, "¿Quieres cerrar sesión?")
        self.assertEqual(cliente.get(salida).status_code, 405)
        self.assertEqual(cliente.post(salida).status_code, 403)

        csrf = cliente.cookies["csrftoken"].value
        self.assertRedirects(
            cliente.post(salida, {"csrfmiddlewaretoken": csrf}),
            reverse("login"),
        )
        self.assertRedirects(cliente.get(inicio), f"{reverse('login')}?next={inicio}")
