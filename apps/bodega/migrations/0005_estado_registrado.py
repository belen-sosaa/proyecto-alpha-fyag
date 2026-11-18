from django.db import migrations, models

from apps.bodega.migration_operations import AddFieldIfMissing


class Migration(migrations.Migration):
    dependencies = [
        ("bodega", "0004_merge_20261118_0012"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
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
            ],
            state_operations=[
                migrations.AddField(
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
            ],
        ),
    ]
