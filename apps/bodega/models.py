from django.conf import settings
from django.db import models


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
    def estado(self):
        if self.stock_fisico <= self.stock_critico:
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