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

## Географические регионы

Для классификации треков используется PostGIS и Overture Maps Divisions. GIS-
зависимости устанавливаются отдельно:

```bash
./scripts/install-system-deps.sh
python -m pip install -e '.[dev,geo]'
```

Скрипт рассчитан на Debian/Ubuntu и сам определяет установленную major-версию
PostgreSQL. При необходимости её можно указать явно:

```bash
POSTGRESQL_MAJOR=14 ./scripts/install-system-deps.sh
```

После создания базы включите PostGIS в рабочей базе и в шаблоне, из которого
Django создаёт тестовые базы:

```bash
sudo -u postgres psql -d rpl \
  -c 'CREATE EXTENSION IF NOT EXISTS postgis;'
sudo -u postgres psql -d template1 \
  -c 'CREATE EXTENSION IF NOT EXISTS postgis;'
python manage.py migrate
```

Официальный клиент Overture может скачать ограниченный срез и сразу передать его
импортёру RPL:

```bash
python manage.py update_overture_regions \
  --bbox=36.8,54.2,40.3,56.9 \
  --release=2026-06-17.0 \
  --country=RU \
  --admin-level=0 \
  --admin-level=1 \
  --admin-level=2 \
  --dry-run
```

Для воспроизводимого импорта заранее скачанных файлов используются отдельные
`division` и `division_area` GeoJSON/GeoJSONSeq:

```bash
python manage.py import_overture_regions \
  --divisions=divisions.geojsonseq \
  --areas=division_areas.geojsonseq \
  --release=2026-06-17.0 \
  --country=RU \
  --admin-level=0 \
  --admin-level=1 \
  --admin-level=2 \
  --dry-run
```

Без `--dry-run` команда атомарно обновляет регионы. Флаг `--authoritative`
разрешает деактивировать отсутствующие в полном входном наборе регионы; его
нельзя использовать для частичного bbox-среза.
