from django.urls import path

from . import views

app_name = "photos"

urlpatterns = [
    path(
        "reports/<uuid:public_id>/photo-batches/",
        views.register_batch,
        name="register-batch",
    )
]
