from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection, transaction
from django.test import TestCase

from .models import Articulo, BitacoraAuditoria, Categoria, MovimientoCardex


class HistorialInalterableTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.usuario = get_user_model().objects.create_user(username="bodeguero")
        categoria = Categoria.objects.create(nombre="Refrigeración")
        cls.articulo = Articulo.objects.create(
            sku="REF-001", descripcion="Filtro", categoria=categoria,
        )
        cls.movimiento = MovimientoCardex.objects.create(
            tipo=MovimientoCardex.Tipo.INGRESO,
            cantidad=4,
            articulo=cls.articulo,
            usuario=cls.usuario,
            stock_resultante=4,
        )
        cls.evento = BitacoraAuditoria.objects.create(
            usuario=cls.usuario,
            accion="CREAR",
            entidad="MovimientoCardex",
            id_registro=cls.movimiento.pk,
        )

    def test_instancias_bloquean_edicion_y_borrado(self):
        for registro in (self.movimiento, self.evento):
            with self.subTest(modelo=type(registro).__name__):
                with self.assertRaisesRegex(ValueError, "modificaciones"):
                    registro.save()
                with self.assertRaisesRegex(ValueError, "eliminaciones"):
                    registro.delete()

    def test_base_de_datos_bloquea_edicion_y_borrado(self):
        for registro, campo, valor in (
            (self.movimiento, "cantidad", 9),
            (self.evento, "accion", "EDITAR"),
        ):
            modelo = type(registro)
            with self.subTest(modelo=modelo.__name__):
                # update() y el DELETE directo prueban los triggers sin usar los métodos de instancia.
                # Cada intento usa una transacción anidada para poder consultar la fila tras el error.
                with self.assertRaises(IntegrityError), transaction.atomic():
                    modelo.objects.filter(pk=registro.pk).update(**{campo: valor})

                tabla = connection.ops.quote_name(modelo._meta.db_table)
                with self.assertRaises(IntegrityError), transaction.atomic():
                    with connection.cursor() as cursor:
                        cursor.execute(f"DELETE FROM {tabla} WHERE id = %s", [registro.pk])

                valor_original = getattr(registro, campo)
                registro.refresh_from_db()
                self.assertEqual(getattr(registro, campo), valor_original)

    def test_movimiento_exige_cantidad_positiva_y_tipo_valido(self):
        for cantidad, tipo in ((0, MovimientoCardex.Tipo.INGRESO), (1, "OTRO")):
            with self.subTest(cantidad=cantidad, tipo=tipo):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    MovimientoCardex.objects.create(
                        tipo=tipo,
                        cantidad=cantidad,
                        articulo=self.articulo,
                        usuario=self.usuario,
                        stock_resultante=4,
                    )
