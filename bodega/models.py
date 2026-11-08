from django.contrib.auth.models import User
from django.db import models


class Categoria(models.Model):
    nombre = models.CharField(max_length=100)

    def __str__(self):
        return self.nombre


class Articulo(models.Model):
    sku = models.CharField(max_length=50, unique=True)
    descripcion = models.CharField(max_length=200)
    categoria = models.ForeignKey(Categoria, on_delete=models.CASCADE)
    stock_fisico = models.IntegerField(default=0)
    estado = models.CharField(max_length=50, default='Stock Normal')

    def __str__(self):
        return f"{self.sku} - {self.descripcion}"


class Obra(models.Model):
    nombre = models.CharField(max_length=200)
    latitud = models.FloatField(null=True, blank=True)
    longitud = models.FloatField(null=True, blank=True)

    def __str__(self):
        return self.nombre


class Despacho(models.Model):
    articulo = models.ForeignKey(Articulo, on_delete=models.PROTECT)
    obra = models.ForeignKey(Obra, on_delete=models.PROTECT)
    cantidad = models.IntegerField()
    fecha_despacho = models.DateTimeField(auto_now_add=True)
    usuario = models.ForeignKey(User, on_delete=models.PROTECT)
    mongodb_id = models.CharField(max_length=100, blank=True, null=True)

    def __str__(self):
        return f"Despacho {self.id} - {self.articulo.sku}"
