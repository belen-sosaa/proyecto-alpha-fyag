from django.conf import settings
from django.db import migrations, models


def mover_tipo_de_contenido(apps, schema_editor, origen, destino):
    tipos = apps.get_model("contenttypes", "ContentType").objects.using(
        schema_editor.connection.alias
    )
    despacho = tipos.filter(app_label=origen, model="despacho").first()
    if despacho is None:
        return
    if tipos.filter(app_label=destino, model="despacho").exists():
        raise RuntimeError("Ya existen tipos de contenido de Despacho en ambas aplicaciones.")

    # Conservar el ID mantiene vinculados los permisos y el historial del administrador.
    despacho.app_label = destino
    despacho.save(update_fields=["app_label"])


def adoptar_tipo_de_contenido(apps, schema_editor):
    mover_tipo_de_contenido(apps, schema_editor, "bodega", "despachos")


def devolver_tipo_de_contenido(apps, schema_editor):
    mover_tipo_de_contenido(apps, schema_editor, "despachos", "bodega")


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("bodega", "0007_mover_despacho"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [
        # Registra en Django la tabla existente, incluidos sus datos y claves foráneas.
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name="Despacho",
                    fields=[
                        ("id", models.BigAutoField(auto_created=True, primary_key=True,
                                                   serialize=False, verbose_name="ID")),
                        ("cantidad", models.IntegerField()),
                        ("fecha_despacho", models.DateTimeField(auto_now_add=True)),
                        ("mongodb_id", models.CharField(max_length=100, blank=True, null=True)),
                        ("articulo", models.ForeignKey(on_delete=models.PROTECT, to="bodega.articulo")),
                        ("obra", models.ForeignKey(on_delete=models.PROTECT, to="bodega.obra")),
                        ("usuario", models.ForeignKey(on_delete=models.PROTECT, to=settings.AUTH_USER_MODEL)),
                    ],
                    options={"db_table": "bodega_despacho"},
                ),
            ],
        ),
        migrations.RunPython(adoptar_tipo_de_contenido, devolver_tipo_de_contenido),
    ]
