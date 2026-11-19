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
from apps.bodega.views import vista_bodega, vista_compras, vista_inicio, vista_inventario
from apps.cuentas.views import AccesoView
from apps.catalogo.views import (
    articulo_crear,
    catalogo_crear,
    catalogo_editar,
    catalogo_eliminar,
    catalogo_listar,
)
from apps.despachos.views import obras_buscar, procesar_despacho

redirigir_a_inicio = RedirectView.as_view(pattern_name="inicio", query_string=True)

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', AccesoView.as_view(), name='login'),
    path('logout/', LogoutView.as_view(), name='logout'),
    path('inicio/', vista_inicio, name='inicio'),
    path('bodega/', vista_bodega, name='bodega_web'),
    path('bodega/inventario/', vista_inventario, name='inventario'),
    path('compras/', vista_compras, name='compras'),
    path('articulos/nuevo/', articulo_crear, name="articulo_crear"),
    path('catalogo/', catalogo_listar, name='catalogo_listar'),
    path('catalogo/nuevo/', catalogo_crear, name='catalogo_crear'),
    path('catalogo/<int:pk>/editar/', catalogo_editar, name='catalogo_editar'),
    path('catalogo/<int:pk>/eliminar/', catalogo_eliminar, name='catalogo_eliminar'),
    path('operaciones/', redirigir_a_inicio),
    path('mobile/', redirigir_a_inicio),
    path('obras/buscar/', obras_buscar, name="obras_buscar"),
    path('despacho/procesar/', procesar_despacho, name='procesar_despacho'),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
