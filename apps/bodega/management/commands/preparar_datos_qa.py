from datetime import date
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import BaseCommand, CommandError, call_command
from django.db import connection, transaction
from pymongo.errors import PyMongoError

from apps.bodega.models import Articulo, Categoria, Obra
from apps.catalogo.especificaciones import guardar_especificaciones, obtener_especificaciones
from apps.catalogo.mongo import obtener_base
from apps.compras.models import DetalleFactura, EntidadComercial, FacturaCompra


ARTICULOS = (
    ("QA-FYAG-FILTRO", "Filtro de refrigeración QA", "Refrigeración", 20, 6, 2, {"diametro_mm": 60, "material": "Acero"}),
    ("QA-FYAG-VALVULA", "Válvula de prueba QA", "Refrigeración", 2, 5, 2, {"diametro_mm": 20, "presion_bar": 8}),
    ("QA-FYAG-CABLE", "Cable de prueba QA", "Material eléctrico", 0, 4, 1, {"longitud_m": 10, "blindado": True}),
)


def cargar_postgres():
    with transaction.atomic():
        usuario, creado = get_user_model().objects.get_or_create(username="fyag_qa_datos")
        # Serializa las cargas de datos y conserva los stocks que ya se usaron en QA.
        usuario = get_user_model().objects.select_for_update().get(pk=usuario.pk)
        if creado:
            usuario.set_unusable_password()
            usuario.save(update_fields=["password"])

        obras = ("QA FYAG Planta Norte", "QA FYAG Cámara Sur")
        for nombre in obras:
            Obra.objects.get_or_create(nombre=nombre)

        fichas = {}
        for sku, descripcion, nombre, stock, minimo, critico, caracteristicas in ARTICULOS:
            categoria, _ = Categoria.objects.get_or_create(nombre=f"QA FYAG · {nombre}")
            articulo, _ = Articulo.objects.get_or_create(sku=sku, defaults={
                "descripcion": descripcion, "categoria": categoria, "stock_fisico": stock,
                "stock_minimo": minimo, "stock_critico": critico,
            })
            if not articulo.categoria.nombre.startswith("QA FYAG · "):
                raise CommandError(f"El código {sku} ya pertenece a un artículo fuera del conjunto QA.")
            fichas[articulo.pk] = caracteristicas
            if sku == "QA-FYAG-FILTRO":
                filtro = articulo

        proveedores = []
        for rut, nombre in (("99999997-2", "QA FYAG Proveedor Norte"), ("99999996-4", "QA FYAG Proveedor Sur")):
            proveedor, _ = EntidadComercial.objects.get_or_create(rut=rut, defaults={"razon_social": nombre})
            if proveedor.razon_social != nombre or proveedor.tipo_entidad != "PROVEEDOR":
                raise CommandError("Un RUT de proveedor QA ya corresponde a otra entidad.")
            proveedores.append(proveedor)

        historico = FacturaCompra.EstadoIngreso.HISTORICO
        for numero, precio in enumerate((8000, 8300, 8550, 8700, 9000), start=1):
            iva = Decimal(precio) * Decimal("0.19")
            factura, creada = FacturaCompra.objects.get_or_create(
                proveedor=proveedores[(numero - 1) % len(proveedores)],
                folio=f"QA-FYAG-{numero:02d}", tipo_documento="FACTURA",
                defaults={
                    "fecha_emision": date(2026, 10, numero), "subtotal_neto": precio,
                    "iva": iva, "total": Decimal(precio) + iva,
                    "usuario_registro": usuario, "estado_ingreso": historico,
                },
            )
            if factura.usuario_registro_id != usuario.pk or factura.estado_ingreso != historico:
                raise CommandError(f"La factura {factura.folio} ya tiene otro origen o estado.")
            if creada:
                DetalleFactura.objects.create(
                    factura=factura, articulo=filtro, cantidad=1,
                    precio_unitario_neto=precio, costo_unitario_neto_final=precio,
                )
            elif not factura.detalles.filter(articulo=filtro).exists():
                raise CommandError(f"La factura {factura.folio} ya existe con un artículo distinto.")
    return fichas


class Command(BaseCommand):
    help = "Aplica las migraciones y carga el conjunto compartido en las bases de QA."

    def handle(self, *args, **options):
        rama_esperada = getattr(settings, "QA_NEON_BRANCH_ID", None)
        if not rama_esperada or connection.vendor != "postgresql" or not settings.MONGO_DB_NAME.endswith("_qa"):
            raise CommandError("Ejecuta el comando con --settings=logistica_fyag.settings_qa.")
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_setting('neon.branch_id', true)")
            if cursor.fetchone()[0] != rama_esperada:
                raise CommandError("La conexión de PostgreSQL no corresponde a la rama QA indicada.")

        try:
            obtener_base().command("ping")
        except PyMongoError as error:
            raise CommandError("MongoDB QA no está disponible. Revisa la conexión y los permisos.") from error

        call_command("migrate", interactive=False, verbosity=options["verbosity"], stdout=self.stdout)
        fichas = cargar_postgres()
        try:
            for articulo_id, caracteristicas in fichas.items():
                if obtener_especificaciones(articulo_id) is None:
                    guardar_especificaciones(articulo_id, caracteristicas)
                Articulo.objects.filter(pk=articulo_id).update(mongodb_especificaciones_id=str(articulo_id))
        except PyMongoError as error:
            raise CommandError(
                "PostgreSQL quedó preparado. Vuelve a ejecutar el comando cuando MongoDB QA esté disponible."
            ) from error
        self.stdout.write(self.style.SUCCESS(
            "QA disponible. 2 obras, 2 categorías, 3 artículos, 2 proveedores, "
            "5 facturas históricas y 3 fichas de MongoDB."
        ))
