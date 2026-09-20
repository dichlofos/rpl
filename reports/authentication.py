import hashlib
import hmac

from django.utils import timezone

from .models import PersonalApiToken


def authenticate_bearer(request):
    header = request.headers.get("Authorization", "")
    scheme, separator, value = header.partition(" ")
    if not separator or scheme.lower() != "bearer":
        return None
    parts = value.split("_", 2)
    if len(parts) != 3 or parts[0] != "rpl":
        return None
    _, token_id, secret = parts
    if not token_id or not secret:
        return None
    token = (
        PersonalApiToken.objects.select_related("user")
        .filter(token_id=token_id, revoked_at__isnull=True)
        .first()
    )
    if token is None:
        return None
    supplied_hash = hashlib.sha256(secret.encode()).hexdigest()
    if not hmac.compare_digest(supplied_hash, token.secret_hash):
        return None
    PersonalApiToken.objects.filter(pk=token.pk).update(last_used_at=timezone.now())
    return token.user


def api_user(request):
    return request.user if request.user.is_authenticated else authenticate_bearer(request)
