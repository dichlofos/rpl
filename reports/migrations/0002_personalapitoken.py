import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("reports", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="PersonalApiToken",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("name", models.CharField(max_length=100, verbose_name="название")),
                (
                    "token_id",
                    models.CharField(
                        editable=False, max_length=16, unique=True, verbose_name="идентификатор"
                    ),
                ),
                (
                    "secret_hash",
                    models.CharField(editable=False, max_length=64, verbose_name="хеш секрета"),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="создан")),
                (
                    "last_used_at",
                    models.DateTimeField(
                        editable=False, null=True, verbose_name="последнее использование"
                    ),
                ),
                (
                    "revoked_at",
                    models.DateTimeField(blank=True, null=True, verbose_name="отозван"),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="api_tokens",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="пользователь",
                    ),
                ),
            ],
            options={
                "verbose_name": "персональный API-токен",
                "verbose_name_plural": "персональные API-токены",
                "ordering": ["-created_at", "-pk"],
            },
        ),
    ]
