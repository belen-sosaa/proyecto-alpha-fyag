from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from apps.cuentas.roles import BODEGUERO, FINANZAS

from .models import EntidadComercial


class FichasComercialesTests(TestCase):
    def setUp(self):
        finanzas = get_user_model().objects.create_user(username="finanzas_fichas")
        finanzas.groups.add(Group.objects.create(name=FINANZAS))
        self.client.force_login(finanzas)
        self.datos = {
            "tipo_entidad": "AMBOS", "rut": "11.111.111-1", "razon_social": "Entidad QA",
            "giro": "Refrigeración", "direccion": "Dirección QA", "banco": "Banco QA",
            "tipo_cuenta": "Corriente", "numero_cuenta": "000123", "correo_pagos": "pagos@example.com",
            "contactos-TOTAL_FORMS": "2", "contactos-INITIAL_FORMS": "0",
            "contactos-0-nombre": "Contacto de proyectos", "contactos-0-correo": "proyectos@example.com",
            "contactos-1-nombre": "Contacto de pagos",
        }

    def test_finanzas_puede_guardar_buscar_y_editar_la_ficha(self):
        self.assertContains(self.client.get(reverse("finanzas")), reverse("entidades"))
        self.assertRedirects(self.client.post(reverse("entidad_crear"), self.datos), reverse("entidades"))
        ficha = EntidadComercial.objects.get(rut="11111111-1")
        self.assertTrue(EntidadComercial.proveedores().filter(pk=ficha.pk).exists())
        self.assertTrue(EntidadComercial.clientes().filter(pk=ficha.pk).exists())
        self.assertEqual(ficha.numero_cuenta, "000123")
        self.assertEqual(ficha.contactos.count(), 2)
        self.assertContains(self.client.get(reverse("entidades"), {"q": "11.111.111-1"}), "Entidad QA")

        contacto, contacto_pagador = ficha.contactos.order_by("pk")
        datos = {**self.datos, "direccion": "Dirección nueva", "contactos-INITIAL_FORMS": "2",
                 "contactos-0-id": contacto.pk, "contactos-1-id": contacto_pagador.pk, "contactos-1-DELETE": "on"}
        self.assertRedirects(self.client.post(reverse("entidad_editar", args=[ficha.pk]), datos), reverse("entidades"))
        ficha.refresh_from_db()
        self.assertEqual(ficha.direccion, "Dirección nueva")
        self.assertEqual(ficha.contactos.count(), 1)

    def test_bodeguero_no_puede_consultar_ni_editar_la_ficha_bancaria(self):
        antigua = EntidadComercial.objects.create(rut="11.111.111-1", razon_social="Entidad anterior")
        self.assertContains(
            self.client.post(reverse("entidad_crear"), self.datos), "Ya existe una entidad con este RUT.",
        )
        usuario = get_user_model().objects.create_user(username="bodeguero_fichas")
        usuario.groups.add(Group.objects.create(name=BODEGUERO))
        self.client.force_login(usuario)
        self.assertEqual(self.client.get(reverse("entidades")).status_code, 403)
        self.assertEqual(self.client.get(reverse("entidad_editar", args=[antigua.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse("entidad_crear"), self.datos).status_code, 403)
