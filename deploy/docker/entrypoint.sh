#!/bin/sh

# Prepare the database
uv run python manage.py migrate
uv run python manage.py loaddata fixtures/new_endpoints.json
uv run python manage.py loaddata fixtures/clusters.json 

# Serve the application with Gunicorn
uv run gunicorn -c gunicorn_asgi.config.py inference_gateway.asgi:application