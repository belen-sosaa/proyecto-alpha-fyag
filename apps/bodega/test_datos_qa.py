from django.core.management import CommandError, call_command
from django.test import TestCase

from apps.compras.models import DetalleFactura, EntidadComercial, FacturaCompra
from .management.commands.preparar_datos_qa import cargar_postgres
from .models import Articulo, Categoria, Obra


class DatosQATests(TestCase):
    def test_carga_repetible_conserva_stock_y_facturas(self):
        fichas = cargar_postgres()
        filtro = Articulo.objects.get(sku="QA-FYAG-FILTRO")
        self.assertEqual(filtro.stock_fisico, 20)
        filtro.stock_fisico = 12
        filtro.save(update_fields=["stock_fisico"])

        self.assertEqual(cargar_postgres(), fichas)
        self.assertEqual(Obra.objects.count(), 2)
        self.assertEqual(Categoria.objects.count(), 2)
        self.assertEqual(Articulo.objects.count(), 3)
        self.assertEqual(EntidadComercial.objects.count(), 2)
        self.assertEqual(FacturaCompra.objects.filter(estado_ingreso="HISTORICO").count(), 5)
        self.assertEqual(DetalleFactura.objects.filter(articulo=filtro).count(), 5)
        filtro.refresh_from_db()
        self.assertEqual(filtro.stock_fisico, 12)

    def test_colision_con_proveedor_ajeno_revierte_la_carga(self):
        EntidadComercial.objects.create(rut="99999997-2", razon_social="Empresa existente")

        with self.assertRaisesMessage(CommandError, "ya corresponde a otra entidad"):
            cargar_postgres()
        self.assertFalse(Articulo.objects.exists())
        self.assertFalse(Obra.objects.exists())
        self.assertFalse(FacturaCompra.objects.exists())

    def test_comando_requiere_configuracion_qa(self):
        with self.assertRaisesMessage(CommandError, "--settings=logistica_fyag.settings_qa"):
            call_command("preparar_datos_qa")
