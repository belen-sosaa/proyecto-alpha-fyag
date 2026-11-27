from django.urls import path

from . import views


app_name = "empleados"
urlpatterns = [
    path("", views.empleados, name="empleados"),
    path("empleados/nuevo/", views.editar_empleado, name="crear"),
    path("empleados/<int:pk>/", views.detalle_empleado, name="detalle"),
    path("empleados/<int:pk>/editar/", views.editar_empleado, name="editar"),
    path("empleados/<int:pk>/vacaciones/nueva/", views.editar_vacacion, name="crear_vacacion"),
    path("empleados/<int:pk>/vacaciones/<int:vacacion_id>/editar/", views.editar_vacacion, name="editar_vacacion"),
]
