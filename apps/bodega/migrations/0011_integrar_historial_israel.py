from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("bodega", "0010_mermas_y_devoluciones"),
        ("bodega", "0007_conteofisico_proveedor_merma_facturacompra_and_more"),
    ]

    operations = [
        # Conserva las tablas históricas. Las operaciones nuevas usan las apps
        # Compras, Conteos y Cardex reconciliadas.
        migrations.SeparateDatabaseAndState(state_operations=[
            migrations.DeleteModel(name="DetalleFacturaCompra"),
            migrations.DeleteModel(name="FacturaCompra"),
            migrations.DeleteModel(name="DevolucionProveedor"),
            migrations.DeleteModel(name="Merma"),
            migrations.DeleteModel(name="Proveedor"),
            migrations.DeleteModel(name="DetalleConteoFisico"),
            migrations.DeleteModel(name="ConteoFisico"),
        ]),
    ]
