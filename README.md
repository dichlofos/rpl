# RPL — Route Planner

Минималистичный сервис на Django для хранения и просмотра GPX-треков на карте
OpenStreetMap.

## Быстрый запуск

Требуется Python 3.10 или новее; рекомендуемая версия — Python 3.13.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
cp .env.example .env
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

По умолчанию пример `.env` использует SQLite, чтобы приложение можно было сразу
посмотреть. Для основной разработки создайте пользователя и базу PostgreSQL и
замените `DATABASE_URL` на строку из `.env.example`.

Создайте пользователя через `/admin/`, войдите через `/accounts/login/` и
загрузите трек на `/tracks/upload/`. Загруженный GPX разбирается при сохранении;
статистика и GeoJSON сохраняются в базе.

## Полезные команды

```bash
make check
make test
make migrate
make run
```

Проект не требует Docker и не зависит от конкретного process manager. Для
развёртывания доступны стандартные `rpl.wsgi:application` и
`rpl.asgi:application`.

Архитектурный план находится в [PLAN.md](PLAN.md).
