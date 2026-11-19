from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("bodega", "0005_estado_registrado")]

    operations = [
        migrations.AddField(
            model_name="perfilusuario",
            name="intentos_fallidos",
            field=models.PositiveSmallIntegerField(default=0, editable=False),
        ),
        migrations.AddField(
            model_name="perfilusuario",
            name="bloqueado_hasta",
            field=models.DateTimeField(blank=True, editable=False, null=True),
        ),
    ]
