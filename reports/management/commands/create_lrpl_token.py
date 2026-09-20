from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from reports.models import PersonalApiToken


class Command(BaseCommand):
    help = "Create a personal Bearer token for LRPL and print it once."

    def add_arguments(self, parser):
        parser.add_argument("username")
        parser.add_argument("--name", default="LRPL")

    def handle(self, *args, **options):
        try:
            user = get_user_model().objects.get(username=options["username"])
        except get_user_model().DoesNotExist as exc:
            raise CommandError("Пользователь не найден.") from exc
        _, raw_token = PersonalApiToken.issue(user, options["name"])
        self.stdout.write(raw_token)
        self.stderr.write("Сохраните токен сейчас: повторно показать его невозможно.")
