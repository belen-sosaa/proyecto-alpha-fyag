from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("bodega", "0013_articulo_precio_lista"),
        ("bodega", "0013_articulo_precio_lista_obra_numero_presupuesto_and_more"),
    ]

    operations = [
        # Ambas ramas comparten una sola columna. El precio se actualiza al confirmar una factura.
        migrations.AlterField(
            model_name="articulo",
            name="precio_lista",
            field=models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, editable=False),
        ),
    ]
