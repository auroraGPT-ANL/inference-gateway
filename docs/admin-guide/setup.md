# Administrator Setup Guide

This guide covers the complete setup process for deploying the FIRST Inference Gateway.

## Prerequisites

- Python 3.11+
- PostgreSQL Server (included in the Docker deployment)
- Poetry
- Docker and Docker Compose (Recommended for Gateway deployment)
- Globus Account
- Access to a compute resource (HPC cluster or a local machine with sufficient resources for the chosen inference server and models)

## Setup Overview

The setup involves two main parts:
1. **Gateway Setup**: Installing and configuring the central API gateway service.
2. **Inference Backend Setup**: Setting up the inference server (like vLLM) and the Globus Compute components on the machine(s) where models will run.

These parts can be done in parallel, but configuration details from each are needed to link them.

---

## Part 1: Gateway Setup

> 📝 Looking for a shorter, task-oriented walkthrough? See the [Docker Quickstart](docker-quickstart.md).

### Step 1: Clone the Repository

```bash
git clone https://github.com/auroraGPT-ANL/inference-gateway.git
cd inference-gateway
```

### Step 2: Choose Deployment Method

**Option A: Docker Deployment (Recommended)**

```bash
# Create necessary directories
mkdir -p logs prometheus

# Configuration is done via the .env file (see next steps)
```

See [Starting the Services](#starting-the-services) for how to run this after configuration.

**Option B: Bare Metal Setup**

```bash
# Set up Python environment with Poetry
poetry config virtualenvs.in-project true
poetry env use python3.11
poetry install

# Activate the environment
poetry shell

# Ensure PostgreSQL server is running and accessible
```

### Step 3: Register Globus Applications

#### Service API Application

The Gateway needs to be registered as a Globus Service API application:

1. Visit [developers.globus.org](https://app.globus.org/settings/developers) and sign in.
2. Under **Register an...**, click on **Register a service API ...**.
3. Complete the registration form:
   - Set **App Name** (e.g., "My Inference Gateway").
   - Add **Redirect URIs**: 
     - Local dev: `http://localhost:8000/complete/globus/`
     - Production: `https://<your-gateway-domain>/complete/globus/`
   - Set **Privacy Policy** and **Terms & Conditions** URLs if applicable.
4. After registration, note the **Client UUID** and generate a **Client Secret**.

#### Add Globus Scope

Export your API client credentials:

```bash
export CLIENT_ID="<Your-Gateway-Service-API-Globus-App-Client-UUID>"
export CLIENT_SECRET="<Your-Gateway-Service-API-Globus-App-Client-Secret>"
```

Create the scope:

```bash
curl -X POST -s --user $CLIENT_ID:$CLIENT_SECRET \
    https://auth.globus.org/v2/api/clients/$CLIENT_ID/scopes \
    -H "Content-Type: application/json" \
    -d '{
        "scope": {
            "name": "Action Provider - all",
            "description": "Access to inference service.",
            "scope_suffix": "action_all",
            "dependent_scopes": [
                {
                    "scope": "73320ffe-4cb4-4b25-a0a3-83d53d59ce4f",
                    "optional": false,
                    "requires_refresh_token": true
                }
            ]
        }
    }'
```

Verify the scope was created:

```bash
curl -s --user $CLIENT_ID:$CLIENT_SECRET https://auth.globus.org/v2/api/clients/$CLIENT_ID
```

#### Service Account Application

Create a Globus Service Account application for Globus Compute endpoints:

1. Visit [developers.globus.org](https://app.globus.org/settings/developers).
2. Click on **Add an App** under your project.
3. Select **Register a service account ...**.
4. Complete the registration form.
5. Note the **Client UUID** and generate a **Client Secret**.

### Step 4: Configure Environment

Create a `.env` file in the project root:

```dotenv
# --- Core Django Settings ---
SECRET_KEY="<generate-a-strong-random-key>"
DEBUG=True # Set to False for production
ALLOWED_HOSTS="localhost,127.0.0.1"

# --- Globus Credentials ---
GLOBUS_APPLICATION_ID="<Your-Gateway-Service-API-Globus-App-Client-UUID>"
GLOBUS_APPLICATION_SECRET="<Your-Gateway-Service-API-Globus-App-Client-Secret>"
POLARIS_ENDPOINT_ID="<Your-Service-Account-Globus-App-Client-UUID>"
POLARIS_ENDPOINT_SECRET="<Your-Service-Account-Globus-App-Client-Secret>"

# --- Database Credentials ---
POSTGRES_DB="inferencegateway"
POSTGRES_USER="inferencedev"
POSTGRES_PASSWORD="inferencedevpwd" # CHANGE THIS for production
PGHOST="postgres" # Use "postgres" for Docker, "localhost" for bare-metal
PGPORT=5432
PGUSER="dataportaldev"
PGPASSWORD="inferencedevpwd"
PGDATABASE="inferencegateway"

# --- Redis ---
REDIS_URL="redis://redis:6379/0" # Use "redis" for Docker

# --- Gateway Specific Settings ---
MAX_BATCHES_PER_USER=2
STREAMING_SERVER_HOST="localhost:8080"
INTERNAL_STREAMING_SECRET="your-internal-streaming-secret-key"
CLI_AUTH_CLIENT_ID="58fdd3bc-e1c3-4ce5-80ea-8d6b87cfb944"
```

Generate a strong secret key:

```bash
python -c 'from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())'
```

### Step 5: Initialize Database

**Docker:**

```bash
docker-compose up -d
docker-compose exec inference-gateway python manage.py makemigrations
docker-compose exec inference-gateway python manage.py migrate
```

**Bare Metal:**

```bash
python manage.py makemigrations
python manage.py migrate
```

---

## Part 2: Inference Backend Setup

This section covers setting up the components on the machine where AI models will run.

### Step 1: Create Python Virtual Environment

Use the same Python version as the Gateway API (Python 3.11):

```bash
# Example with conda
conda create -n vllm-env python=3.11 -y
conda activate vllm-env

# Example with python venv
python3.11 -m venv vllm-env
source vllm-env/bin/activate
```

### Step 2: Install Inference Server and Globus Compute

Install vLLM:

```bash
git clone https://github.com/vllm-project/vllm.git
cd vllm
pip install -e .
```

Install Globus Compute:

```bash
pip install globus-compute-sdk globus-compute-endpoint
```

### Step 3: Register Globus Compute Functions

Export your Service Account credentials:

```bash
export GLOBUS_COMPUTE_CLIENT_ID="<Value-of-POLARIS_ENDPOINT_ID-from-.env>"
export GLOBUS_COMPUTE_CLIENT_SECRET="<Value-of-POLARIS_ENDPOINT_SECRET-from-.env>"
```

Register the inference function:

```bash
cd path/to/inference-gateway/compute-functions

# Register vLLM inference function
python vllm_register_function_with_streaming.py
# Note the Function UUID

# Register status function (optional but recommended)
python qstat_register_function.py
# Note the Function UUID
```

### Step 4: Configure Globus Compute Endpoint

Create and configure an endpoint:

```bash
globus-compute-endpoint configure my-compute-endpoint
```

Edit the generated `config.yaml` file:
- Add your function UUIDs to `allowed_functions`
- Configure `worker_init` to activate your environment
- Adjust executor settings for your hardware

See [local-vllm-endpoint.yaml](../../compute-endpoints/local-vllm-endpoint.yaml) for an example configuration.

Start the endpoint:

```bash
globus-compute-endpoint start my-compute-endpoint
# Note the Endpoint UUID
```

---

## Part 3: Connect Gateway and Backend

### Step 1: Update Fixtures

Edit `fixtures/endpoints.json` or `fixtures/federated_endpoints.json`:

**Non-federated example (endpoints.json):**

```json
[
    {
        "model": "resource_server.endpoint",
        "pk": 1,
        "fields": {
            "endpoint_slug": "local-vllm-facebook-opt-125m",
            "cluster": "local",
            "framework": "vllm",
            "model": "facebook/opt-125m",
            "api_port": 8001,
            "endpoint_uuid": "<your-endpoint-uuid>",
            "function_uuid": "<your-function-uuid>",
            "batch_endpoint_uuid": "",
            "batch_function_uuid": "",
            "allowed_globus_groups": ""
        }
    }
]
```

**Federated example (federated_endpoints.json):**

```json
[
    {
        "model": "resource_server.federatedendpoint",
        "pk": 1,
        "fields": {
            "name": "OPT 125M (Federated)",
            "slug": "federated-opt-125m",
            "target_model_name": "facebook/opt-125m",
            "description": "Federated access point for the facebook/opt-125m model.",
            "targets": [
                {
                    "cluster": "local",
                    "framework": "vllm",
                    "model": "facebook/opt-125m",
                    "endpoint_slug": "local-vllm-facebook-opt-125m",
                    "endpoint_uuid": "<your-endpoint-uuid>",
                    "function_uuid": "<your-function-uuid>",
                    "api_port": 8001
                }
            ]
        }
    }
]
```

### Step 2: Load Fixtures

**Docker:**

```bash
docker-compose exec inference-gateway python manage.py loaddata fixtures/endpoints.json
```

**Bare Metal:**

```bash
python manage.py loaddata fixtures/endpoints.json
```

---

## Starting the Services

### Gateway

**Docker:**

```bash
docker-compose up --build -d
```

**Bare Metal (Development):**

```bash
poetry shell
python manage.py runserver 0.0.0.0:8000
```

**Bare Metal (Production with Gunicorn):**

```bash
poetry shell
poetry run gunicorn \
    inference_gateway.asgi:application \
    -k uvicorn.workers.UvicornWorker \
    -b 0.0.0.0:8000 \
    --workers 5 \
    --log-level info
```

### Inference Backend

Ensure the Globus Compute endpoint is running:

```bash
globus-compute-endpoint start <my-endpoint-name>
```

---

## Production Considerations

For production deployments, use Nginx as a reverse proxy:

### Example Nginx Configuration

```nginx
upstream app_server {
    server 127.0.0.1:8000 fail_timeout=0;
}

server {
    listen 80;
    server_name your-gateway-domain.org;

    client_max_body_size 4G;

    location /static/ {
        alias /path/to/inference-gateway/static/;
    }

    location / {
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Host $http_host;
        proxy_redirect off;
        proxy_pass http://app_server;
    }
}
```

Remember to:
- Collect static files: `python manage.py collectstatic`
- Configure SSL/TLS certificates
- Set up rate limiting and security headers
- Configure firewall rules

---

## Monitoring

If deployed via Docker Compose with monitoring enabled:

- **Grafana**: http://localhost:3000 (admin/admin)
- **Prometheus**: http://localhost:9090

The dashboard includes:
- Application metrics (request rates, latency, error rates)
- System metrics (CPU, memory, disk I/O)
- Database metrics (connection counts, query performance)
- Custom Gateway metrics (inference rates, token counts)

---

## Troubleshooting

**Database Connection Errors**
- Check `.env` variables match your deployment context
- Verify PostgreSQL is running and accessible
- Check firewall rules and `pg_hba.conf`

**Globus Auth Errors**
- Ensure Redirect URIs match in Globus Developer portal
- Verify credentials in `.env` are correct
- Check scope was created successfully

**Compute Endpoint Issues**
- Check endpoint logs: `~/.globus_compute/<endpoint_name>/endpoint.log`
- Verify function UUIDs in `config.yaml`
- Ensure environment activation works correctly

**500 Server Errors**
- Check gateway logs: `docker-compose logs inference-gateway`
- Look for Python tracebacks in Gunicorn logs
- Verify all required environment variables are set

