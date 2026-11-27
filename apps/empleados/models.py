from django.db import models


class Empleado(models.Model):
    rut = models.CharField("RUT", max_length=12, unique=True)
    nombres = models.CharField(max_length=120)
    apellidos = models.CharField(max_length=120)
    cargo = models.CharField(max_length=120)
    correo = models.EmailField("correo laboral", blank=True)
    telefono = models.CharField("teléfono", max_length=30, blank=True)
    fecha_ingreso = models.DateField()
    tipo_contrato = models.CharField("tipo de contrato", max_length=100, blank=True)
    fecha_termino_contrato = models.DateField("término del contrato", null=True, blank=True)
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ("apellidos", "nombres", "pk")
        constraints = [
            models.CheckConstraint(
                condition=models.Q(fecha_termino_contrato__gte=models.F("fecha_ingreso"))
                | models.Q(fecha_termino_contrato__isnull=True),
                name="rrhh_contrato_fechas_validas",
            ),
        ]

    def __str__(self):
        return f"{self.nombres} {self.apellidos}"


class Vacacion(models.Model):
    empleado = models.ForeignKey(Empleado, related_name="vacaciones", on_delete=models.PROTECT)
    desde = models.DateField()
    hasta = models.DateField()

    class Meta:
        ordering = ("-desde", "-pk")
        constraints = [
            models.CheckConstraint(
                condition=models.Q(hasta__gte=models.F("desde")),
                name="rrhh_vacacion_fechas_validas",
            ),
        ]
