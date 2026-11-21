from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("bodega", "0006_cardex_y_bitacora_inalterables"),
    ]

    operations = [
        # El modelo sale del estado de bodega. Su tabla se reutiliza en despachos.
        migrations.SeparateDatabaseAndState(
            state_operations=[migrations.DeleteModel(name="Despacho")],
        ),
    ]
