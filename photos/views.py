import json

from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from reports.authentication import authenticate_bearer
from reports.models import TravelReport
from tracks.permissions import can_manage

from .services import register_photo_batch


@csrf_exempt
@require_POST
def register_batch(request, public_id):
    user = authenticate_bearer(request)
    if user is None:
        return JsonResponse({"error": "Требуется персональный Bearer-токен."}, status=401)
    report = get_object_or_404(TravelReport, public_id=public_id)
    if not can_manage(user, report):
        return JsonResponse({"error": "Нет доступа к отчёту."}, status=403)
    try:
        payload = json.loads(request.body)
        result = register_photo_batch(user, report, payload)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Неверный JSON."}, status=400)
    except ValidationError as exc:
        return JsonResponse({"error": " ".join(exc.messages)}, status=400)
    return JsonResponse({"schema_version": 1, **result})
