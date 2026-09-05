from django.core.exceptions import PermissionDenied


def can_manage(user, obj):
    return user.is_authenticated and (user.is_superuser or obj.owner_id == user.pk)


def require_manage(user, obj):
    if not can_manage(user, obj):
        raise PermissionDenied("Изменять объект может только его владелец.")
