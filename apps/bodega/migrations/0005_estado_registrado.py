from django.db import migrations, models

from apps.bodega.migration_operations import AddFieldIfMissing


class Migration(migrations.Migration):
    dependencies = [
        ("bodega", "0004_merge_20261118_0012"),
    ]

    operations = [
        # Retira solo la definición anterior. Los datos de la columna estado se conservan.
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveField(model_name="articulo", name="estado"),
            ],
        ),
        # Una base que ya ejecutó la versión anterior de 0002 puede necesitar la columna.
        AddFieldIfMissing(
            model_name="articulo",
            name="estado_registrado",
            field=models.CharField(
                db_column="estado",
                default="Stock Normal",
                editable=False,
                max_length=50,
            ),
            preserve_default=False,
        ),
    ]
