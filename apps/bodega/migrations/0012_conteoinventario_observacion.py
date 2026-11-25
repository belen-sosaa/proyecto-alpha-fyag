from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("bodega", "0011_integrar_historial_israel")]

    operations = [
        migrations.AddField(
            model_name="conteoinventario",
            name="observacion",
            field=models.TextField(blank=True, default=""),
            preserve_default=False,
        ),
    ]
