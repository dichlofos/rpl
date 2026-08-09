import django.core.validators
from django.db import migrations, models

import tracks.models


class Migration(migrations.Migration):
    dependencies = [("tracks", "0002_track_public_id_remove_slug")]

    operations = [
        migrations.AlterField(
            model_name="track",
            name="gpx_file",
            field=models.FileField(
                upload_to=tracks.models.anonymized_gpx_path,
                validators=[django.core.validators.FileExtensionValidator(["gpx"])],
                verbose_name="GPX-файл",
            ),
        )
    ]
