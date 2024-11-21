# Base container image
FROM python:3.11.9-slim

# Define container's working directory
WORKDIR /app

# Install GCC (preventing error: Building wheels for collected packages: screen)
RUN apt-get update
RUN apt-get install -y gcc

# Setup environment
COPY ./requirements.txt ./
RUN pip install -r requirements.txt

# Copy Django project into the container
COPY ./ ./
RUN rm .env
RUN rm db.sqlite3

# Copy environment variables (temporary, should be added through GitLab secret key/values)
#COPY ./.env ./

# Initialize and fill Django database
RUN python manage.py migrate --no-input
RUN python manage.py loaddata fixtures/endpoints.json

# Create the folder where the gunicorn backend log files will be written
RUN mkdir /var/log/inference-service/

# Deploy Django backend and spinup the Django-Gunicorn service
COPY ./entrypoint.sh /
ENTRYPOINT ["sh", "/entrypoint.sh"]