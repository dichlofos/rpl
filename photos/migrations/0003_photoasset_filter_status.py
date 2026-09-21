from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("photos", "0002_photoasset_logical_day")]

    operations = [
        migrations.AddField(
            model_name="photoasset",
            name="filter_status",
            field=models.CharField(
                choices=[
                    ("unreviewed", "не проверена"),
                    ("selected", "оставлена"),
                    ("rejected", "отклонена как похожая"),
                ],
                default="unreviewed",
                max_length=10,
                verbose_name="результат фильтрации",
            ),
        ),
    ]
