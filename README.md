[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
![Build](https://github.com/auroraGPT-ANL/inference-gateway/workflows/Django/badge.svg)

# Inference Gateway
A RESTful API Gateway code to authenticate and authorize inference requests coming from within or outside Argonne. Trusted users can submit jobs to the ALCFss compute endpoints. 

## Documentation

The API/Usage documentation can be found [here](https://github.com/argonne-lcf/inference-endpoints).

## Prerequisites

- Python 3.8+
- PostgreSQL
- [Poetry](https://python-poetry.org/docs/#installation)

## Installation

**Note:** All commands listed below should be executed within this folder where this README.md file is located.

### Virtual Environment (Python 3.11.9)
Install all required dependencies using poetry:

```bash
poetry config virtualenvs.in-project true
poetry env use ..path-to-your-targetted-python-executable..
poetry install
```

Alternatively, if you want to use `pip install`, a requirements.txt file is available.

### Configuration
Create a file named ``.env`` with the following content:

```bash
SECRET_KEY="<some-super-secret-key>"
GLOBUS_APPLICATION_ID="<Globus-vLLM-API-client-identity>"
GLOBUS_APPLICATION_SECRET="<Globus-vLLM-API-client-secret>"
POLARIS_ENDPOINT_ID="<compute-endpoint-app-identity>"
POLARIS_ENDPOINT_SECRET="<compute-endpoint-add-secret>"
DEBUG=False
GLOBUS_GROUPS="<globus-group-uuid>"
GLOBUS_POLICIES="<globus-policy-uuid>"
PGHOST="localhost"
PGPORT=5432
PGDATABASE="<Postgres DB Name>"
PGUSER="<Postgres User Name>"
ENABLE_ASYNC=True
```

The official Inference Group and Policy UUIDs for `GLOBUS_GROUPS` and `GLOBUS_POLICIES` are 1e56984c-d5ae-11ee-8844-b93550bcf92a and 41689588-6a11-4ce9-aa24-f196ca7bf774, respectively.

### Local Database

Activate your virtual environment!

```bash
source .venv/bin/activate
```
or
```bash
poetry shell
```

Set up the initial database (``db.sqlite3``):
```bash
python manage.py migrate
```

Fill the database with the various inference endpoints
```bash
python manage.py loaddata fixtures/endpoints.json
```

## Run Server

```bash
python manage.py runserver
```

## Run Bulk Inference API

Install with poetry and run from within a `poetry shell` session:

```
poetry run agpt-bulk-cli
```


## Migration To Postgres DB from local SQLlite

First dump existing data

```bash
python manage.py dumpdata --natural-foreign --natural-primary > datadump.json
```

Install required packages

```bash
sudo apt install postgresql postgresql-contrib
sudo apt install pgloader
sudo apt install libpq-dev
pip install psycopg2-binary
pip install psycopg2
```

Make a copy of sqllite and create super user. 

```bash
cp db.sqlite3 db_backup.sqlite3
#sudo -u postgres psql
>$ psql postgres
psql (14.13 (Homebrew))
Type "help" for help.

postgres=# CREATE USER dataportaldev WITH PASSWORD '';
postgres=# CREATE DATABASE inferencegateway OWNER dataportaldev;
postgres=# GRANT ALL PRIVILEGES ON DATABASE inferencegateway TO dataportaldev;
postgres=# \q
```

Also create a .pgpass file which looks similar to this for postgres password `hostname:port:database:username:password`


Before you make the migrations change the `pgloader.load` file with appropriate path to sqllite and specify the details of postgres.

```bash
source .env
python manage.py migrate
pgloader pgloader.load
python reset_cursor.py
```
