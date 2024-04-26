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
SOCIAL_AUTH_GLOBUS_KEY="<Globus-vLLM-API-CLIENT-ID>"
SOCIAL_AUTH_GLOBUS_SECRET="<Globus-vLLM-API-CLIENT-SECRET>"
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