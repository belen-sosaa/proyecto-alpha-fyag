from django.conf import settings
from django.db import models
from django.db.models import Q


class EntidadComercial(models.Model):
    rut = models.CharField(max_length=12, unique=True)
    razon_social = models.CharField(max_length=200)
    tipo_entidad = models.CharField(max_length=15, default="PROVEEDOR")
    contacto = models.CharField(max_length=150, blank=True, null=True)
    datos_bancarios = models.TextField(blank=True, null=True)

    @classmethod
    def proveedores(cls):
        return cls.objects.filter(Q(tipo_entidad__iexact="PROVEEDOR") | Q(tipo_entidad__iexact="AMBOS"))

    class Meta:
        db_table = "bodega_entidadcomercial"
        verbose_name_plural = "Entidades comerciales"

    def __str__(self):
        return self.razon_social


class FacturaCompra(models.Model):
    class TipoDocumento(models.TextChoices):
        FACTURA = "FACTURA", "Factura"
        GUIA = "GUIA_DESPACHO", "Guía de despacho"
        NOTA_CREDITO = "NOTA_CREDITO", "Nota de crédito"

    class EstadoIngreso(models.TextChoices):
        PENDIENTE = "PENDIENTE", "Pendiente"
        CONFIRMADO = "CONFIRMADO", "Confirmado"
        HISTORICO = "HISTORICO", "Histórico"

    proveedor = models.ForeignKey(EntidadComercial, on_delete=models.PROTECT)
    folio = models.CharField(max_length=50)
    tipo_documento = models.CharField(max_length=20, choices=TipoDocumento.choices, default=TipoDocumento.FACTURA)
    # Una factura puede respaldar una guía y una nota de crédito puede corregir una factura.
    documento_origen = models.ForeignKey(
        "self", on_delete=models.PROTECT, related_name="documentos_asociados", null=True, blank=True,
    )
    fecha_emision = models.DateField()
    subtotal_neto = models.DecimalField(max_digits=12, decimal_places=2)
    iva = models.DecimalField(max_digits=12, decimal_places=2)
    total = models.DecimalField(max_digits=12, decimal_places=2)
    usuario_registro = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    estado_ingreso = models.CharField(
        max_length=10, choices=EstadoIngreso.choices, default=EstadoIngreso.PENDIENTE,
    )

    class Meta:
        db_table = "bodega_facturacompra"
        unique_together = [("folio", "proveedor", "tipo_documento")]
        # La base también impide asociar dos facturas a la misma guía si se guardan a la vez.
        constraints = [
            models.UniqueConstraint(
                fields=["documento_origen"],
                condition=Q(tipo_documento="FACTURA", documento_origen__isnull=False),
                name="compras_factura_unica_por_guia",
            ),
        ]

    def __str__(self):
        return f"{self.get_tipo_documento_display()} {self.folio} · {self.proveedor}"


class DetalleFactura(models.Model):
    factura = models.ForeignKey(FacturaCompra, on_delete=models.PROTECT, related_name="detalles")
    articulo = models.ForeignKey("bodega.Articulo", on_delete=models.PROTECT)
    cantidad = models.PositiveIntegerField()
    precio_unitario_neto = models.DecimalField(
        max_digits=12, decimal_places=2, db_column="precio_unitario_lista",
    )
    descuento_1 = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    descuento_2 = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    descuento_3 = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    costo_unitario_neto_final = models.DecimalField(
        max_digits=12, decimal_places=2, db_column="costo_neto_final",
    )
    # Es el precio de venta propuesto en esta línea. El costo de compra se guarda por separado.
    precio_lista = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    class Meta:
        db_table = "bodega_detallefactura"
