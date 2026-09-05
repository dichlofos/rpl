# RPL — Route Planner

Минималистичный сервис на Django для хранения, просмотра и
редактирования GPX-треков на карте OpenStreetMap.

## Возможности

- Загрузка и хранение GPX-треков с привязкой к владельцу.
- Список треков в виде карточек или таблицы.
- Просмотр маршрута на карте с отметками старта и финиша.
- Высотный профиль под картой: высота по расстоянию, просмотр точки на карте
  при наведении на график и обновление при редактировании трека.
- Расчёт дистанции, набора и сброса высоты, продолжительности и числа точек.
- Ручное редактирование: перемещение точек и обрезка начала или конца трека.
- Выбор прямоугольной области и удаление точек внутри или снаружи неё.
- Удаление лишних точек в окне W точек, если расстояние между крайними
  не превышает d метров; крайние точки сохраняются. После удаления обход
  продолжается с конца окна, иначе окно сдвигается на одну точку.
  Сегменты обрабатываются отдельно, неполный остаток сохраняется.
- Отмена несохранённых правок и явное сохранение результата.
- Скачивание актуальной версии трека в формате GPX.
- Автоматическая классификация треков по административным регионам
  через PostGIS и Overture Maps.

## Быстрый запуск

Требуются Python 3.10 или новее и PostgreSQL с PostGIS; рекомендуемая
версия Python — 3.13. SQLite не поддерживается, так как модели проекта
используют GIS-поля.

На Debian/Ubuntu системные зависимости можно установить готовым скриптом:

```bash
./scripts/install-system-deps.sh
```

Создайте пользователя, базу и включите PostGIS в рабочей и тестовой базах:

```bash
sudo -u postgres psql -c "CREATE ROLE rpl LOGIN PASSWORD 'password';"
sudo -u postgres createdb --owner=rpl rpl
sudo -u postgres psql -d rpl -c 'CREATE EXTENSION IF NOT EXISTS postgis;'
sudo -u postgres psql -d template1 -c 'CREATE EXTENSION IF NOT EXISTS postgis;'
```

Затем установите и запустите проект:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
cp .env.example .env
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

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

Для классификации треков используются PostGIS и Overture Maps Divisions. Клиент Overture
устанавливается дополнительно:

```bash
python -m pip install -e '.[dev,geo]'
```

Скрипт рассчитан на Debian/Ubuntu и сам определяет установленную major-версию
PostgreSQL. При необходимости её можно указать явно:

```bash
POSTGRESQL_MAJOR=14 ./scripts/install-system-deps.sh
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
