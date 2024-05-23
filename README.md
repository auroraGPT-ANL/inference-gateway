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
INFERENCE_SERVICE_URL="http://<inference-service-url-ending-with-slash>/"
DEBUG=False
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

## Run Server

```bash
python manage.py runserver
```