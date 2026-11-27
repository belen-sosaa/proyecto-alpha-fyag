# URL configuration for logistica_fyag project.
#
# The `urlpatterns` list routes URLs to views. For more information please see:
#     https://docs.djangoproject.com/en/4.2/topics/http/urls/
# Examples:
# Function views
#     1. Add an import:  from my_app import views
#     2. Add a URL to urlpatterns:  path('', views.home, name='home')
# Class-based views
#     1. Add an import:  from other_app.views import Home
#     2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
# Including another URLconf
#     1. Import the include() function: from django.urls import include, path
#     2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
from django.contrib import admin
from django.conf import settings
from django.contrib.auth.views import LogoutView
from django.conf.urls.static import static
from django.urls import include, path
from django.views.generic import RedirectView
from apps.bodega.conteos import (
    consultar_discrepancias,
    nuevo_conteo,
    registrar_conteo,
    vista_conteos,
    vista_discrepancias,
)
from apps.bodega.egresos import registrar_egreso, vista_egreso
from apps.bodega.views import vista_bodega, vista_cardex
from apps.compras.entidades import gestionar_entidades
from apps.compras.views import (
    confirmar_ingreso,
    consultar_ultimos_precios,
    exportar_precios_excel,
    listar_proveedores,
    registrar_factura,
    vista_ultimos_precios,
    vista_confirmar_compra,
    vista_registrar_compra,
)
from apps.cuentas.views import AccesoView
from apps.catalogo.views import (
    articulo_crear,
    articulo_detalle,
    catalogo_crear,
    catalogo_editar,
    catalogo_eliminar,
    catalogo_listar,
)
from apps.despachos.views import (
    articulo_por_codigo,
    comprobante_despacho,
    descargar_comprobante_despacho,
    obras_buscar,
    procesar_despacho,
    registrar_recepcion,
    vista_despacho,
)
from apps.despachos.preparacion import gestionar_obras

urlpatterns = [
    path('admin/', admin.site.urls),
    path("reportes/", include("apps.reportes.urls")),
    path('', AccesoView.as_view(), name='login'),
    path('logout/', LogoutView.as_view(http_method_names=["post", "options"]), name='logout'),
    path('inicio/', vista_bodega, {'modulo': 'inicio'}, name='inicio'),
    path('operaciones/', RedirectView.as_view(pattern_name='inicio', query_string=True)),
    path('bodega/', vista_bodega, name='bodega_web'),
    path('bodega/inventario/', RedirectView.as_view(pattern_name='catalogo_listar', query_string=True), name='inventario'),
    path('bodega/cardex/', vista_cardex, name='cardex'),
    path('bodega/conteos/', vista_conteos, name='conteos'),
    path('bodega/conteos/nuevo/', nuevo_conteo, name='nuevo_conteo'),
    path('bodega/conteos/<int:conteo_id>/', vista_discrepancias, name='discrepancias_conteo'),
    path('bodega/mermas/', vista_egreso, {'tipo': 'MERMA'}, name='merma_crear'),
    path('bodega/devoluciones/', vista_egreso, {'tipo': 'DEVOLUCION'}, name='devolucion_crear'),
    path('auditoria/', vista_bodega, {'modulo': 'auditoria'}, name='auditoria'),
    path('compras/', vista_bodega, {'modulo': 'compras'}, name='compras'),
    path('compras/nueva/', vista_registrar_compra, name='vista_registrar_compra'),
    path('compras/<int:factura_id>/confirmar/', vista_confirmar_compra, name='vista_confirmar_compra'),
    path('catalogo/<int:pk>/precios/excel/', exportar_precios_excel, name='articulo_exportar_precios_excel'),
    path('compras/precios/', vista_ultimos_precios, name='ultimos_precios'),
    path('finanzas/', vista_bodega, {'modulo': 'finanzas'}, name='finanzas'),
    path('finanzas/entidades/', gestionar_entidades, name='entidades'),
    path('finanzas/entidades/nueva/', gestionar_entidades, {'nueva': True}, name='entidad_crear'),
    path('finanzas/entidades/<int:pk>/', gestionar_entidades, name='entidad_editar'),
    path('articulos/nuevo/', articulo_crear, name="articulo_crear"),
    path('articulos/por-codigo/', articulo_por_codigo, name='articulo_por_codigo'),
    path('catalogo/', catalogo_listar, name='catalogo_listar'),
    path('catalogo/nuevo/', catalogo_crear, name='catalogo_crear'),
    path('catalogo/<int:pk>/detalle/', articulo_detalle, name='articulo_detalle'),
    path('bodega/mermas/nueva/', RedirectView.as_view(pattern_name='merma_crear')),
    path('bodega/devoluciones/nueva/', RedirectView.as_view(pattern_name='devolucion_crear')),
    path('bodega/conteo/nuevo/', RedirectView.as_view(pattern_name='nuevo_conteo'), name='conteo_crear'),
    path('bodega/conteo/discrepancias/', RedirectView.as_view(pattern_name='conteos'), name='conteo_discrepancias'),
    path('catalogo/<int:pk>/editar/', catalogo_editar, name='catalogo_editar'),
    path('catalogo/<int:pk>/eliminar/', catalogo_eliminar, name='catalogo_eliminar'),
    path('despachos/', vista_despacho, name='despachos'),
    path('despachos/', vista_despacho, name='despachos_web'),
    path('despachos/<int:despacho_id>/comprobante/', comprobante_despacho, name='comprobante_despacho'),
    path(
        'despachos/<int:despacho_id>/comprobante/pdf/',
        descargar_comprobante_despacho,
        name='descargar_comprobante_despacho',
    ),
    path('despachos/<int:despacho_id>/recepcion/', registrar_recepcion, name='registrar_recepcion'),
    path('mobile/', RedirectView.as_view(pattern_name='despachos', query_string=True), name='bodega_mobile'),
    path('obras/buscar/', obras_buscar, name="obras_buscar"),
    path('obras/', gestionar_obras, name='obras'),
    path('obras/nueva/', gestionar_obras, {'nueva': True}, name='obra_crear'),
    path('obras/<int:pk>/', gestionar_obras, name='obra_editar'),
    path('despacho/procesar/', procesar_despacho, name='procesar_despacho'),
    path('api/proveedores/', listar_proveedores, name='listar_proveedores'),
    path('api/articulos/<int:articulo_id>/ultimos-precios/', consultar_ultimos_precios, name='api_ultimos_precios'),
    path('api/facturas/', registrar_factura, name='registrar_factura'),
    path('api/ingresos/', confirmar_ingreso, name='confirmar_ingreso'),
    path('api/conteos/', registrar_conteo, name='registrar_conteo'),
    path('api/conteos/<int:conteo_id>/discrepancias/', consultar_discrepancias, name='api_discrepancias_conteo'),
    path('api/mermas/', registrar_egreso, {'tipo': 'MERMA'}, name='registrar_merma'),
    path('api/devoluciones/', registrar_egreso, {'tipo': 'DEVOLUCION'}, name='registrar_devolucion'),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
