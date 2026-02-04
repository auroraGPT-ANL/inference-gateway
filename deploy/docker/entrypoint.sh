#!/bin/sh

# Prepare the database
python manage.py migrate
python manage.py loaddata fixtures/new_endpoints.json
python manage.py loaddata fixtures/clusters.json 

# Serve the application with Gunicorn
gunicorn -c gunicorn_asgi.config.py inference_gateway.asgi:application