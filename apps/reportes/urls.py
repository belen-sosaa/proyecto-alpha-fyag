from django.urls import path

from . import views


app_name = "reportes"

urlpatterns = [
    path("ingresos/", views.reporte_ingresos, name="ingresos"),
    path("egresos/", views.reporte_egresos, name="egresos"),
    path("inventario/", views.reporte_inventario, name="inventario"),
    path("ingresos/exportar/xlsx/", views.exportar_ingresos_excel, name="ingresos_excel"),
    path("ingresos/exportar/pdf/", views.exportar_ingresos_pdf, name="ingresos_pdf"),
    path("egresos/exportar/xlsx/", views.exportar_egresos_excel, name="egresos_excel"),
    path("egresos/exportar/pdf/", views.exportar_egresos_pdf, name="egresos_pdf"),
    path("inventario/exportar/xlsx/", views.exportar_inventario_excel, name="inventario_excel"),
    path("inventario/exportar/pdf/", views.exportar_inventario_pdf, name="inventario_pdf"),
]
