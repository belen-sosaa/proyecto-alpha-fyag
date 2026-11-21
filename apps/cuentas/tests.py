from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse


class SalidaTests(TestCase):
    # Se activa la validación CSRF para comprobar que solo la confirmación puede cerrar la sesión.
    def test_salida_pide_confirmacion_y_cierra_sesion_por_post_con_csrf(self):
        usuario = get_user_model().objects.create_user(username="cuenta_de_prueba")
        cliente = Client(enforce_csrf_checks=True)
        cliente.force_login(usuario)
        inicio = reverse("inicio")
        salida = reverse("logout")

        panel = cliente.get(inicio)
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
