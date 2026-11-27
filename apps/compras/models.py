from django.conf import settings
from django.db import models
from django.db.models import Q


class EntidadComercial(models.Model):
    class Tipo(models.TextChoices):
        CLIENTE = "CLIENTE", "Cliente"
        PROVEEDOR = "PROVEEDOR", "Proveedor"
        AMBOS = "AMBOS", "Cliente y proveedor"

    rut = models.CharField(max_length=12, unique=True)
    razon_social = models.CharField(max_length=200)
    tipo_entidad = models.CharField(max_length=15, choices=Tipo.choices, default=Tipo.PROVEEDOR)
    contacto = models.CharField(max_length=150, blank=True, null=True)
    datos_bancarios = models.TextField(blank=True, null=True)
    giro = models.CharField(max_length=200, blank=True)
    direccion = models.CharField("dirección", max_length=200, blank=True)
    region = models.CharField("región", max_length=100, blank=True)
    comuna = models.CharField(max_length=100, blank=True)
    ciudad = models.CharField(max_length=100, blank=True)
    representante_legal = models.CharField(max_length=200, blank=True)
    rut_representante = models.CharField("RUT del representante", max_length=12, blank=True)
    banco = models.CharField(max_length=100, blank=True)
    tipo_cuenta = models.CharField("tipo de cuenta", max_length=50, blank=True)
    numero_cuenta = models.CharField("número de cuenta", max_length=50, blank=True)
    correo_pagos = models.EmailField("correo para pagos", blank=True)

    @classmethod
    def proveedores(cls):
        return cls.objects.filter(Q(tipo_entidad__iexact="PROVEEDOR") | Q(tipo_entidad__iexact="AMBOS"))

    @classmethod
    def clientes(cls):
        return cls.objects.filter(Q(tipo_entidad__iexact="CLIENTE") | Q(tipo_entidad__iexact="AMBOS"))

    class Meta:
        db_table = "bodega_entidadcomercial"
        verbose_name_plural = "Entidades comerciales"

    def __str__(self):
        return self.razon_social


class ContactoEntidad(models.Model):
    entidad = models.ForeignKey(EntidadComercial, on_delete=models.CASCADE, related_name="contactos")
    nombre = models.CharField(max_length=150)
    rut = models.CharField("RUT", max_length=12, blank=True)
    cargo = models.CharField(max_length=100, blank=True)
    correo = models.EmailField(blank=True)
    telefono = models.CharField("teléfono", max_length=30, blank=True)


class FacturaCompra(models.Model):
    class EstadoIngreso(models.TextChoices):
        PENDIENTE = "PENDIENTE", "Pendiente"
        CONFIRMADO = "CONFIRMADO", "Confirmado"
        HISTORICO = "HISTORICO", "Histórico"

    proveedor = models.ForeignKey(EntidadComercial, on_delete=models.PROTECT)
    folio = models.CharField(max_length=50)
    tipo_documento = models.CharField(max_length=20, default="FACTURA")
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

    class Meta:
        db_table = "bodega_detallefactura"
