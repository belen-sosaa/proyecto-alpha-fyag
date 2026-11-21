import re

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


def normalizar_rut(valor):
    """Valida el dígito verificador y devuelve el RUT sin puntos."""
    valor = valor.strip().upper()

    if re.fullmatch(
        r"[1-9][0-9]{0,2}(?:\.[0-9]{3}){1,2}-[0-9K]",
        valor,
    ):
        valor = valor.replace(".", "")

    partes = re.fullmatch(
        r"([1-9][0-9]{0,7})-?([0-9K])",
        valor,
    )

    if partes:
        numero, digito = partes.groups()
        suma = sum(
            int(cifra) * (indice % 6 + 2)
            for indice, cifra in enumerate(reversed(numero))
        )

        if digito == "0123456789K"[(11 - suma % 11) % 11]:
            return f"{numero}-{digito}"

    raise ValidationError("Ingresa un RUT válido.")


class PerfilUsuario(models.Model):
    usuario = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
    )

    rut = models.CharField(
        "RUT",
        max_length=12,
        unique=True,
    )

    intentos_fallidos = models.PositiveSmallIntegerField(default=0, editable=False)
    bloqueado_hasta = models.DateTimeField(null=True, blank=True, editable=False)

    # Columna del esquema de Neon. Los permisos se consultan en los grupos de Django.
    rol = models.CharField(
        max_length=20,
        blank=True,
        editable=False,
    )

    class Meta:
        verbose_name = "Perfil de usuario"
        verbose_name_plural = "Perfiles de usuario"

    def clean(self):
        try:
            self.rut = normalizar_rut(self.rut)
        except ValidationError as error:
            raise ValidationError({"rut": error}) from error

    def save(self, *args, **kwargs):
        self.clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.usuario.get_username()


class Categoria(models.Model):
    nombre = models.CharField(
        max_length=100,
        unique=True,
    )

    class Meta:
        db_table = "bodega_categoria"
        verbose_name = "Categoría"
        verbose_name_plural = "Categorías"

    def __str__(self):
        return self.nombre


class Articulo(models.Model):
    sku = models.CharField(
        max_length=50,
        unique=True,
        verbose_name="Código interno",
    )

    codigo_barras = models.CharField(
        max_length=100,
        unique=True,
        null=True,
        blank=True,
        verbose_name="Código de barras",
    )

    descripcion = models.CharField(
        max_length=200,
        verbose_name="Descripción",
    )

    categoria = models.ForeignKey(
        "Categoria",
        on_delete=models.PROTECT,
        verbose_name="Categoría",
    )

    unidad_medida = models.CharField(
        max_length=30,
        default="unidad",
        verbose_name="Unidad de medida",
    )

    ubicacion_fisica = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        verbose_name="Ubicación en bodega",
    )

    stock_fisico = models.PositiveIntegerField(
        default=0,
        verbose_name="Stock físico",
    )

    stock_minimo = models.PositiveIntegerField(
        default=0,
        verbose_name="Stock mínimo",
    )

    stock_critico = models.PositiveIntegerField(
        default=0,
        verbose_name="Stock crítico",
    )

    # Corresponde a la columna "estado" existente en Neon.
    estado_registrado = models.CharField(
        max_length=50,
        db_column="estado",
        editable=False,
    )

    mongodb_especificaciones_id = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        editable=False,
    )

    class Meta:
        db_table = "bodega_articulo"
        verbose_name = "Insumo"
        verbose_name_plural = "Insumos"

    @property
    def es_stock_critico(self):
        return self.stock_fisico <= self.stock_critico

    @property
    def estado(self):
        if self.es_stock_critico:
            return "Stock Crítico"
        return "Stock Normal"

    def save(self, *args, **kwargs):
        self.estado_registrado = self.estado

        campos = kwargs.get("update_fields")

        if campos is not None:
            campos = set(campos)

            if campos:
                campos.add("estado_registrado")
                kwargs["update_fields"] = campos

        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.sku} - {self.descripcion}"


class Obra(models.Model):
    nombre = models.CharField(max_length=200)
    latitud = models.FloatField(null=True, blank=True)
    longitud = models.FloatField(null=True, blank=True)

    def __str__(self):
        return self.nombre


class Despacho(models.Model):
    articulo = models.ForeignKey(
        "Articulo",
        on_delete=models.PROTECT,
    )

    obra = models.ForeignKey(
        "Obra",
        on_delete=models.PROTECT,
    )

    cantidad = models.IntegerField()

    fecha_despacho = models.DateTimeField(
        auto_now_add=True,
    )

    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
    )

    mongodb_id = models.CharField(
        max_length=100,
        blank=True,
        null=True,
    )

    def __str__(self):
        return f"Despacho {self.pk} - {self.articulo.sku}"


class BitacoraAuditoria(models.Model):
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
    )

    accion = models.CharField(max_length=50)
    entidad = models.CharField(max_length=100)
    id_registro = models.PositiveBigIntegerField()

    valor_anterior = models.JSONField(
        null=True,
        blank=True,
    )

    valor_nuevo = models.JSONField(
        null=True,
        blank=True,
    )

    detalle = models.TextField(blank=True)

    fecha_hora = models.DateTimeField(
        auto_now_add=True,
    )

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError(
                "La bitácora no permite modificaciones."
            )

        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError(
            "La bitácora no permite eliminaciones."
        )


class Merma(models.Model):
    articulo = models.ForeignKey(Articulo, on_delete=models.PROTECT)
    cantidad = models.PositiveIntegerField()
    motivo = models.TextField()
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    fecha = models.DateTimeField(auto_now_add=True)


class Proveedor(models.Model):
    nombre = models.CharField(max_length=150)
    rut = models.CharField(max_length=20, blank=True)
    contacto = models.CharField(max_length=150, blank=True)

    def __str__(self):
        return self.nombre


class FacturaCompra(models.Model):
    numero_factura = models.CharField(max_length=50)
    proveedor = models.ForeignKey(Proveedor, on_delete=models.PROTECT)
    fecha = models.DateTimeField(auto_now_add=True)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    total_neto = models.DecimalField(max_digits=12, decimal_places=2)


class DetalleFacturaCompra(models.Model):
    factura = models.ForeignKey(
        FacturaCompra,
        on_delete=models.PROTECT,
        related_name="detalles",
    )
    articulo = models.ForeignKey(Articulo, on_delete=models.PROTECT)
    cantidad = models.PositiveIntegerField()
    precio_unitario = models.DecimalField(max_digits=12, decimal_places=2)
    descuento_1 = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    descuento_2 = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    descuento_3 = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    subtotal_neto = models.DecimalField(max_digits=12, decimal_places=2)


class DevolucionProveedor(models.Model):
    proveedor = models.ForeignKey(Proveedor, on_delete=models.PROTECT)
    articulo = models.ForeignKey(Articulo, on_delete=models.PROTECT)
    cantidad = models.PositiveIntegerField()
    motivo = models.TextField()
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    fecha = models.DateTimeField(auto_now_add=True)


class ConteoFisico(models.Model):
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    fecha = models.DateTimeField(auto_now_add=True)
    observacion = models.TextField(blank=True)


class DetalleConteoFisico(models.Model):
    conteo = models.ForeignKey(
        ConteoFisico,
        on_delete=models.CASCADE,
        related_name="detalles",
    )
    articulo = models.ForeignKey(Articulo, on_delete=models.PROTECT)
    stock_sistema = models.PositiveIntegerField()
    stock_contado = models.PositiveIntegerField()

    @property
    def diferencia(self):
        return self.stock_contado - self.stock_sistema
