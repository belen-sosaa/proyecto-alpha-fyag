from django.urls import NoReverseMatch, reverse


GERENTE = "Gerente"
ADMIN_TI = "Administrador TI"
ADMIN_BODEGA = "Administrador de Bodega"
BODEGUERO = "Bodeguero"
FINANZAS = "Administrativo de Finanzas"
SUPERVISOR = "Supervisor de Obra"
AUDITOR = "Auditor"
ADMIN_GENERAL_LEGADO = "Administrador General"

ROLES = (GERENTE, ADMIN_TI, ADMIN_BODEGA, BODEGUERO, FINANZAS, SUPERVISOR, AUDITOR)
SIN_ROL = "Sin rol"

# Cada módulo define su etiqueta, icono, ruta y descripción.
MODULOS = {
    "panel": ("Inicio", "fa-gauge-high", "inicio", "Alertas y resumen de la sesión"),
    "catalogo": ("Catálogo", "fa-boxes-stacked", "catalogo_listar", "Consulta de existencias"),
    "cardex": ("Auditoría", "fa-clipboard-check", "auditoria", "Auditoría de movimientos"),
    "despacho": ("Despachos", "fa-truck-fast", "despachos_web", "Despachos de materiales y recepción en obra"),
    "merma": ("Mermas", "fa-triangle-exclamation", "merma_crear", "Bajas con justificación"),
    "compras": ("Compras", "fa-file-invoice-dollar", "compras", "Presupuestos y órdenes"),
    "usuarios": ("Usuarios", "fa-users-gear", "usuarios", "Gestión de cuentas"),
}

# Módulos asociados a cada rol. Las vistas usan esta lista para construir la navegación.
ACCESO = {
    GERENTE: ("panel", "catalogo", "cardex", "compras"),
    ADMIN_TI: ("panel", "usuarios"),
    ADMIN_BODEGA: ("panel", "catalogo", "cardex", "despacho", "merma", "compras"),
    BODEGUERO: ("panel", "catalogo", "despacho", "merma", "compras"),
    FINANZAS: ("panel", "compras", "cardex"),
    SUPERVISOR: ("panel", "despacho"),
    AUDITOR: ("panel", "cardex", "catalogo"),
    ADMIN_GENERAL_LEGADO: ("panel", "catalogo", "cardex"),
}

# Acciones que el servidor permite a cada rol, incluso al acceder a una ruta directamente.
FACULTADES = {
    "ver_catalogo": (GERENTE, ADMIN_BODEGA, BODEGUERO, FINANZAS, AUDITOR, ADMIN_GENERAL_LEGADO),
    "emitir_oc": (FINANZAS,),
    "crear_presupuesto": (BODEGUERO, ADMIN_BODEGA),
    "registrar_merma": (BODEGUERO, ADMIN_BODEGA),
    "ingresar_articulo": (ADMIN_BODEGA, ADMIN_GENERAL_LEGADO),
    "editar_articulo": (ADMIN_BODEGA, ADMIN_GENERAL_LEGADO),
    "eliminar_articulo": (ADMIN_BODEGA, ADMIN_GENERAL_LEGADO),
    "gestionar_usuarios": (ADMIN_TI,),
    "ver_costos": (GERENTE, FINANZAS, ADMIN_BODEGA),
    "despachar": (BODEGUERO, ADMIN_BODEGA),
}


def rol_principal(user):
    """Devuelve el primer grupo reconocido en orden de creación."""
    if not user or not user.is_authenticated:
        return SIN_ROL

    grupos = (
        user.groups.filter(name__in=(*ROLES, ADMIN_GENERAL_LEGADO))
        .order_by("pk")
        .values_list("name", flat=True)
    )
    for grupo in grupos:
        if grupo in ROLES:
            return grupo
    # El grupo antiguo se toma como respaldo cuando la cuenta aún no tiene un rol actual.
    return ADMIN_GENERAL_LEGADO if ADMIN_GENERAL_LEGADO in grupos else SIN_ROL


def claves_de(user):
    """Devuelve las claves de navegación del rol principal."""
    return ACCESO.get(rol_principal(user), ())


def modulos_de(user):
    """Prepara los módulos habilitados para las plantillas."""
    modulos = []
    for clave in claves_de(user):
        etiqueta, icono, nombre_ruta, descripcion = MODULOS[clave]
        try:
            url = reverse(nombre_ruta)
        except NoReverseMatch:
            url = "#"
        modulos.append({
            "clave": clave,
            "etiqueta": etiqueta,
            "icono": icono,
            "url": url,
            "descripcion": descripcion,
        })
    return modulos


def permisos_de(user):
    """Devuelve el rol y sus facultades para las plantillas."""
    rol = rol_principal(user)
    return {"rol": rol, **{clave: rol in roles for clave, roles in FACULTADES.items()}}


def puede(user, facultad):
    return rol_principal(user) in FACULTADES.get(facultad, ())
