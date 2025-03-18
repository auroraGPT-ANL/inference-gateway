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
GLOBUS_GROUPS="
<globus-group-uuid-1>
<globus-group-uuid-2>
...
"
GLOBUS_POLICIES="
<globus-policy-uuid-1>
<globus-policy-uuid-2>
...
"
AUTHORIZED_IDPS='
{
    "<identity-provider-name-1>" : "<identity-provider-uuid-1>",
    "<identity-provider-name-2>" : "<identity-provider-uuid-2>"
}
'

PGHOST="localhost"
PGPORT=5432
PGDATABASE="<Postgres DB Name>"
PGUSER="<Postgres User Name>"

MAX_BATCHES_PER_USER=1
```

`GLOBUS_POLICIES` should be High Assurance policies to enforce a check on the identity provider used to authenticate. `AUTHORIZED_IDPS` are additional manual authorization checks that can be enforced in the API directly.

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
