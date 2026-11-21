from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from apps.bodega.models import Articulo, Categoria
from apps.cuentas.roles import BODEGUERO, SUPERVISOR


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
