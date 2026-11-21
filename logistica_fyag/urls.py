"""
URL configuration for logistica_fyag project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/4.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.conf import settings
from django.contrib.auth.views import LogoutView
from django.conf.urls.static import static
from django.urls import path
from django.views.generic import RedirectView
from apps.bodega.views import vista_bodega
from apps.compras.views import confirmar_ingreso, listar_proveedores, registrar_factura
from apps.cuentas.views import AccesoView
from apps.catalogo.views import (
    articulo_crear,
    catalogo_crear,
    catalogo_editar,
    catalogo_eliminar,
    catalogo_listar,
)
from apps.despachos.views import obras_buscar, procesar_despacho, vista_despacho

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', AccesoView.as_view(), name='login'),
    path('logout/', LogoutView.as_view(), name='logout'),
    path('bodega/', vista_bodega, name='bodega_web'),
    path('auditoria/', vista_bodega, {'modulo': 'auditoria'}, name='auditoria'),
    path('compras/', vista_bodega, {'modulo': 'compras'}, name='compras'),
    path('finanzas/', vista_bodega, {'modulo': 'finanzas'}, name='finanzas'),
    path('articulos/nuevo/', articulo_crear, name="articulo_crear"),
    path('catalogo/', catalogo_listar, name='catalogo_listar'),
    path('catalogo/nuevo/', catalogo_crear, name='catalogo_crear'),
    path('catalogo/<int:pk>/editar/', catalogo_editar, name='catalogo_editar'),
    path('catalogo/<int:pk>/eliminar/', catalogo_eliminar, name='catalogo_eliminar'),
    path('despachos/', vista_despacho, name='despachos'),
    path('mobile/', RedirectView.as_view(pattern_name='despachos', query_string=True), name='bodega_mobile'),
    path('obras/buscar/', obras_buscar, name="obras_buscar"),
    path('despacho/procesar/', procesar_despacho, name='procesar_despacho'),
    path('api/proveedores/', listar_proveedores, name='listar_proveedores'),
    path('api/facturas/', registrar_factura, name='registrar_factura'),
    path('api/ingresos/', confirmar_ingreso, name='confirmar_ingreso'),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
