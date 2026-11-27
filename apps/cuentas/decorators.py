from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied

from .roles import ROLES, puede


def rol_requerido(*roles):
    # Esta variante compara los grupos de la cuenta directamente.
    def decorator(view):
        @login_required
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            permitido = request.user.groups.filter(
                name__in=roles
            ).exists()

            if not permitido:
                raise PermissionDenied(
                    "No tienes permiso para realizar esta acción."
                )

            return view(request, *args, **kwargs)

        return wrapper

    return decorator


def facultad_requerida(*facultades):
    # Protege una vista con una facultad declarada en roles.py.
    def decorator(view):
        @login_required
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            # login_required lleva al login sin sesión. Con sesión y sin facultad, Django responde 403.
            if not any(puede(request.user, facultad) for facultad in facultades):
                raise PermissionDenied("Tu rol no tiene permiso para esta acción.")
            return view(request, *args, **kwargs)

        return wrapper

    return decorator


def roles_validos():
    return ROLES
