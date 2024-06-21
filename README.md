[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
![Build](https://github.com/auroraGPT-ANL/inference-gateway/workflows/Django/badge.svg)

# Inference Gateway
Prototype to create a checkpoint to authorize inference requests coming from outside Argonne.

## Installation

**Note:** All commands listed below should be executed within this folder where this README.md file is located.

### Virtual Environment
Install all required dependencies using poetry:

```bash
poetry config virtualenvs.in-project true
poetry install
```

Alternatively, if you want to use `pip install`, a requirements.txt file is available.

### Configuration
Create a file named ``.env`` with the following content:

```bash
SECRET_KEY="<SOME_SUPER_SECRET_KEY>"
GLOBUS_APPLICATION_ID="<Globus-vLLM-API-CLIENT-ID>"
GLOBUS_APPLICATION_SECRET="<Globus-vLLM-API-CLIENT-SECRET>"
POLARIS_ENDPOINT_ID="<compute-endpoint-app-identity>"
POLARIS_ENDPOINT_SECRET="<compute-endpoint-add-secret>"
DEBUG=False
GLOBUS_GROUPS="
1e56984c-d5ae-11ee-8844-b93550bcf92a
"
GLOBUS_POLICIES="
41689588-6a11-4ce9-aa24-f196ca7bf774
"
```

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
