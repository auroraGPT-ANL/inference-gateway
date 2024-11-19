#! /bin/sh

# Start Gunicorn async service
gunicorn -c gunicorn_asgi.config.py inference_gateway.asgi:application