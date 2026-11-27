from django.apps import AppConfig


class EmpleadosConfig(AppConfig):
    name = "apps.empleados"
    # Mantiene las tablas y la migración ya registradas con este identificador.
    label = "rrhh"
    verbose_name = "Empleados"
