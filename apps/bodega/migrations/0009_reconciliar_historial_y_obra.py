from django.db import migrations, models

from apps.bodega.migration_operations import AddFieldIfMissing


class Migration(migrations.Migration):
    dependencies = [
        ("bodega", "0006_bloqueo_acceso_rut"),
        ("bodega", "0008_conteos_inventario"),
        ("compras", "0001_facturas_e_ingresos"),
    ]

    operations = [
        # Las columnas ya existen en Neon. La instalación desde cero también debe crearlas.
        AddFieldIfMissing(
            model_name="obra", name="estado",
            field=models.CharField(default="ACTIVA", max_length=20),
        ),
        AddFieldIfMissing(
            model_name="obra", name="presupuesto_asignado",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=12),
        ),
        AddFieldIfMissing(
            model_name="obra", name="cliente",
            field=models.ForeignKey(
                "compras.EntidadComercial", on_delete=models.PROTECT, null=True, blank=True,
            ),
        ),
    ]
