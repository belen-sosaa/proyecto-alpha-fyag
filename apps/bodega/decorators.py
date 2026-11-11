from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied


def rol_requerido(*roles):
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
