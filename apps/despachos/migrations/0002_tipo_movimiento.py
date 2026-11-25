from django.db import migrations, models

from apps.bodega.migration_operations import AddFieldIfMissing


class Migration(migrations.Migration):
    dependencies = [
        ("despachos", "0001_adoptar_despacho"),
    ]

    operations = [
        # Neon ya exige este campo. Las bases nuevas lo agregan al migrar.
        AddFieldIfMissing(
            model_name="despacho",
            name="tipo_movimiento",
            field=models.CharField(max_length=20, default="DESPACHO"),
        ),
    ]
