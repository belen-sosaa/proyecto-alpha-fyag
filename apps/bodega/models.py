from django.conf import settings
from django.db import models

class Categoria(models.Model):
    nombre = models.CharField(max_length=100)

    def __str__(self):
        return self.nombre


class Articulo(models.Model):
    sku = models.CharField(max_length=50, unique=True)
    codigo_barras = models.CharField(
        max_length=100,
        unique=True,
        null=True,
        blank=True,
    )
    descripcion = models.CharField(max_length=200)
    categoria = models.ForeignKey(
        "Categoria",
        on_delete=models.CASCADE,
    )
    unidad_medida = models.CharField(max_length=30, default="unidad")
    stock_fisico = models.PositiveIntegerField(default=0)
    stock_minimo = models.PositiveIntegerField(default=0)
    stock_critico = models.PositiveIntegerField(default=0)

    @property
    def estado(self):
        if self.stock_fisico <= self.stock_critico:
            return "Stock Crítico"
        return "Stock Normal"

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
    fecha_despacho = models.DateTimeField(auto_now_add=True)
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
    valor_anterior = models.JSONField(null=True, blank=True)
    valor_nuevo = models.JSONField(null=True, blank=True)
    detalle = models.TextField(blank=True)
    fecha_hora = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError("La bitácora no permite modificaciones.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("La bitácora no permite eliminaciones.")