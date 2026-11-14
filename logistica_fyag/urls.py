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
from django.contrib.auth.views import LogoutView
from django.urls import path
from apps.bodega import views

urlpatterns = [
    # 1. Administración de Django y Autenticación
    path("admin/", admin.site.urls),
    path("", views.AccesoView.as_view(), name="login"),
    path("logout/", LogoutView.as_view(), name="logout"),

    # 2. Panel Principal de Bodega (Web)
    path("bodega/", views.vista_bodega, name="bodega_web"),
    path("articulos/nuevo/", views.articulo_crear, name="articulo_crear"),

    # 3. Operación Mobile-First (Tablet), Autocompletado de Obras y Despachos
    path("mobile/", views.vista_mobile, name="bodega_mobile"),
    path("obras/buscar/", views.obras_buscar, name="obras_buscar"),
    path("despacho/procesar/", views.procesar_despacho, name="procesar_despacho"),

    # 4. CRUD de Insumos / Catálogo con RBAC, Filtros y Bloqueo de Eliminación
    path("catalogo/", views.catalogo_listar, name="catalogo_listar"),# READ (Listar y filtrar)
    path("catalogo/nuevo/", views.catalogo_crear, name="catalogo_crear"),# CREATE (Crear insumo)
    path("catalogo/<int:pk>/editar/", views.catalogo_editar, name="catalogo_editar"),# UPDATE (Editar insumo)
    path("catalogo/<int:pk>/eliminar/", views.catalogo_eliminar, name="catalogo_eliminar"),# DELETE (Eliminar con bloqueo)
]
