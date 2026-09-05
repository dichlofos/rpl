.PHONY: run test test-js check migrate superuser

JS ?= node

run:
	python manage.py runserver

test:
	python manage.py test

test-js:
	$(JS) tests/js/track-simplify.test.mjs
	$(JS) tests/js/track-elevation.test.mjs
	$(JS) tests/js/track-geometry.test.mjs

check:
	python manage.py check

migrate:
	python manage.py migrate

superuser:
	python manage.py createsuperuser
