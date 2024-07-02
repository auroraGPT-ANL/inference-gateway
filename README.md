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
GLOBUS_GROUP_MANAGER_ID="<Globus-group-manager-identity>"
GLOBUS_GROUP_MANAGER_SECRET="<Globus-group-manager-secret>"
DEBUG=False
GLOBUS_GROUPS=""
GLOBUS_POLICIES="
41689588-6a11-4ce9-aa24-f196ca7bf774
"
```

To enable Globus Group check, the `GLOBUS_GROUP_MANAGER` client must be a member in all allowed Globus Groups. This can be done with the Globus CLI with the following command `globus group member add <group-uuid> <GLOBUS_GROUP_MANAGER_ID>@clients.auth.globus.org`. The current Inference Group UUID that should be added in `GLOBUS_GROUPS` is 1e56984c-d5ae-11ee-8844-b93550bcf92a.

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

Install with poetry and run from within `poetry shell`:

```
poetry run agpt-bulk-cli
```
