import hashlib
import secrets
import uuid
from typing import ClassVar

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class TravelReport(models.Model):
    name = models.CharField("название", max_length=200)
    description = models.TextField("описание", blank=True)
    public_id = models.UUIDField(
        "публичный идентификатор", default=uuid.uuid4, unique=True, editable=False
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="владелец",
        related_name="travel_reports",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    track_group = models.ForeignKey(
        "tracks.TrackGroup",
        verbose_name="исходная группа треков",
        related_name="travel_reports",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    tracks = models.ManyToManyField(
        "tracks.Track", through="ReportTrack", related_name="travel_reports"
    )
    created_at = models.DateTimeField("создан", auto_now_add=True)
    updated_at = models.DateTimeField("изменён", auto_now=True)

    class Meta:
        ordering: ClassVar[list[str]] = ["-updated_at", "-pk"]
        verbose_name = "отчёт о путешествии"
        verbose_name_plural = "отчёты о путешествиях"

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        if self._state.adding and not self.track_group_id:
            raise ValidationError({"track_group": "Выберите исходную группу треков."})
        if self.track_group_id and self.owner_id and self.track_group.owner_id != self.owner_id:
            raise ValidationError(
                {"track_group": "У отчёта и группы треков должен быть один владелец."}
            )

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class ReportTrack(models.Model):
    report = models.ForeignKey(
        TravelReport, on_delete=models.CASCADE, related_name="track_links", verbose_name="отчёт"
    )
    track = models.ForeignKey(
        "tracks.Track",
        on_delete=models.CASCADE,
        related_name="report_links",
        verbose_name="трек",
    )
    position = models.PositiveIntegerField("порядок", default=0)

    class Meta:
        ordering: ClassVar[list[str]] = ["position", "pk"]
        constraints: ClassVar[list] = [
            models.UniqueConstraint(fields=["report", "track"], name="unique_report_track"),
            models.UniqueConstraint(
                fields=["report", "position"], name="unique_report_track_position"
            ),
        ]
        verbose_name = "трек отчёта"
        verbose_name_plural = "треки отчёта"

    def __str__(self):
        return f"{self.report}: {self.track}"

    def clean(self):
        super().clean()
        if not self.report_id or not self.track_id:
            return
        track_changed = (
            self._state.adding
            or type(self).objects.filter(pk=self.pk).exclude(track_id=self.track_id).exists()
        )
        if not track_changed:
            return
        group_id = self.report.track_group_id
        if group_id is None:
            raise ValidationError({"track": "У отчёта больше нет исходной группы треков."})
        if not self.track.group_memberships.filter(group_id=group_id).exists():
            raise ValidationError({"track": "Трек не входит в исходную группу отчёта."})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class PersonalApiToken(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="api_tokens",
        verbose_name="пользователь",
    )
    name = models.CharField("название", max_length=100)
    token_id = models.CharField("идентификатор", max_length=16, unique=True, editable=False)
    secret_hash = models.CharField("хеш секрета", max_length=64, editable=False)
    created_at = models.DateTimeField("создан", auto_now_add=True)
    last_used_at = models.DateTimeField("последнее использование", null=True, editable=False)
    revoked_at = models.DateTimeField("отозван", null=True, blank=True)

    class Meta:
        ordering: ClassVar[list[str]] = ["-created_at", "-pk"]
        verbose_name = "персональный API-токен"
        verbose_name_plural = "персональные API-токены"

    def __str__(self):
        return f"{self.user}: {self.name}"

    @classmethod
    def issue(cls, user, name):
        token_id = secrets.token_hex(8)
        secret = secrets.token_urlsafe(32)
        token = cls.objects.create(
            user=user,
            name=name,
            token_id=token_id,
            secret_hash=hashlib.sha256(secret.encode()).hexdigest(),
        )
        return token, f"rpl_{token_id}_{secret}"

    def revoke(self):
        if self.revoked_at is None:
            self.revoked_at = timezone.now()
            self.save(update_fields=["revoked_at"])
