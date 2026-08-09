.PHONY: run test check migrate superuser

run:
	python manage.py runserver

test:
	python manage.py test

check:
	python manage.py check

migrate:
	python manage.py migrate

superuser:
	python manage.py createsuperuser
