from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("photos", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="photoasset",
            name="logical_day",
            field=models.PositiveSmallIntegerField(
                blank=True, null=True, verbose_name="логический день"
            ),
        ),
        migrations.AddField(
            model_name="photoasset",
            name="day_confirmed",
            field=models.BooleanField(default=False, verbose_name="логический день подтверждён"),
        ),
        migrations.AddConstraint(
            model_name="photoasset",
            constraint=models.CheckConstraint(
                condition=models.Q(logical_day__isnull=True)
                | (models.Q(logical_day__gte=1) & models.Q(logical_day__lte=99)),
                name="photo_logical_day_between_1_and_99",
            ),
        ),
        migrations.AddConstraint(
            model_name="photoasset",
            constraint=models.CheckConstraint(
                condition=models.Q(day_confirmed=False) | models.Q(logical_day__isnull=False),
                name="confirmed_photo_has_logical_day",
            ),
        ),
    ]
