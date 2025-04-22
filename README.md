[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
![Build](https://github.com/auroraGPT-ANL/inference-gateway/workflows/Django/badge.svg)

# Inference Gateway for FIRST toolkit

FIRST (Federated Inference Resource Scheduling Toolkit) is a system that enables LLM (Large Language Model) inference as a service, allowing secure, remote execution of LLMs through an [OpenAI](https://platform.openai.com/docs/overview)-compatible API. FIRST's Inference Gateway is a RESTful API that validates and authorizes inference requests to scientific computing clusters using [Globus Auth](https://www.globus.org/globus-auth-service) and [Globus Compute](https://www.globus.org/compute).

## Table of Contents

- [System Architecture](#system-architecture)
- [Prerequisites](#prerequisites)
- [Setup Overview](#setup-overview)
- [Gateway Setup](#gateway-setup)
  - [Installation (Docker or Bare Metal)](#installation-docker-or-bare-metal)
  - [Register Globus Application](#register-globus-application)
  - [Configure Environment (.env)](#configure-environment-env)
  - [Initialize Gateway Database](#initialize-gateway-database)
- [Inference Backend Setup (Remote/Local)](#inference-backend-setup-remotelocal)
  - [Install Inference Server (e.g., vLLM)](#install-inference-server-eg-vllm)
  - [Register Globus Compute Functions](#register-globus-compute-functions)
  - [Configure and Start Globus Compute Endpoint](#configure-and-start-globus-compute-endpoint)
- [Connecting Gateway and Backend](#connecting-gateway-and-backend)
  - [Update Fixtures](#update-fixtures)
  - [Load Fixtures](#load-fixtures)
- [Starting the Services](#starting-the-services)
  - [Gateway (Docker or Bare Metal)](#gateway-docker-or-bare-metal)
  - [Inference Backend (Globus Compute Endpoint)](#inference-backend-globus-compute-endpoint)
- [Verifying the Setup](#verifying-the-setup)
- [Production Considerations (Nginx)](#production-considerations-nginx)
- [Monitoring](#monitoring)
- [Troubleshooting](#troubleshooting)

## System Architecture

![System Architecture](./inference_gateway_architecture_focused.png)

The Inference Gateway consists of several components:
- **API Gateway**: [Django](https://www.djangoproject.com/)-based [REST](https://www.django-rest-framework.org/)/[Ninja](https://django-ninja.dev/) API that handles authorization and request routing.
- **Globus Auth**: Authentication and authorization service.
- **Globus Compute Endpoints**: Remote execution framework on HPC clusters (or local machines).
- **Inference Server Backend**: (e.g., [vLLM](https://docs.vllm.ai/en/latest/)) High-performance inference service for LLMs running alongside the Globus Compute Endpoint.

## Prerequisites

- Python 3.11+
- [PostgreSQL](https://www.postgresql.org/docs/) Server (can be run via Docker)
- [Poetry](https://python-poetry.org/docs/#installation)
- [Docker](https://docs.docker.com/) and [Docker Compose](https://docs.docker.com/compose/) (Recommended for Gateway deployment)
- [Globus Account](https://www.globus.org/)
- Access to a compute resource (HPC cluster or a local machine with sufficient resources for the chosen inference server and models)

## Setup Overview

The setup involves two main parts:
1.  **Gateway Setup**: Installing and configuring the central API gateway service.
2.  **Inference Backend Setup**: Setting up the inference server (like vLLM) and the Globus Compute components on the machine(s) where models will run.

These parts can be done in parallel, but configuration details from each are needed to link them.

## Gateway Setup

This section covers setting up the central Django application.

### Installation (Docker or Bare Metal)

Clone the repository first:
```bash
git clone https://github.com/auroraGPT-ANL/inference-gateway.git
cd inference-gateway
```

**Option 1: Docker Deployment (Recommended)**

```bash
# Create necessary directories needed by docker-compose.yml
mkdir -p logs prometheus
# Create a basic prometheus config if you don't have one
# echo "global:\n  scrape_interval: 15s\nscrape_configs:\n  - job_name: 'prometheus'\n    static_configs:\n      - targets: ['localhost:9090']" > prometheus/prometheus.yml

# Configuration is done via the .env file (see next steps)
# Build and start services (in background)
docker-compose -f docker-compose.yml up --build -d
```
*See [Starting the Services](#starting-the-services) for how to run this after configuration.* 
*See `docker-compose.yml` for details on included services (Postgres, Redis, optional monitoring).* 

**Option 2: Bare Metal Setup / Local Development**

```bash
# Set up Python environment with Poetry
poetry config virtualenvs.in-project true
poetry env use python3.11
poetry install

# Activate the environment
poetry shell

# Ensure PostgreSQL server is running and accessible.
# Configuration is done via environment variables or .env (see next steps).
```

### Register Globus Application

To handle authentication, the Gateway needs to be registered as a Globus application:

1.  Visit [developers.globus.org](https://developers.globus.org) and sign in.
2.  Select **Register a new application**.
3.  Choose **Portal / Science Gateway** as the application type.
4.  Create a new project or select an existing one.
5.  Complete the registration form:
    *   Set **App Name** (e.g., "My Inference Gateway").
    *   Add **Redirect URIs**. For local development with the default Django server (`runserver`), use `http://localhost:8000/complete/globus/`. For production, use `https://<your-gateway-domain>/complete/globus/`.
    *   Note the **Scopes** required (consult Globus documentation if unsure).
    *   Set **Privacy Policy** and **Terms & Conditions** URLs if applicable.
6.  After registration, you will receive a **Client ID** and generate a **Client Secret**. **You will need these for the `.env` configuration.**

### Configure Environment (.env)

Create a `.env` file in the project root (`inference-gateway/`). This file is used by both Docker and bare-metal setups (if using `python-dotenv`).

```dotenv
# --- Core Django Settings ---
SECRET_KEY="<generate-a-strong-random-key>" # Use e.g., python -c 'from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())'
DEBUG=True # Set to False for production
ALLOWED_HOSTS="localhost,127.0.0.1" # Add your gateway domain/IP for production

# --- Globus Auth Credentials (from Step 2) ---
GLOBUS_APPLICATION_ID="<Your-Globus-App-Client-ID>"
GLOBUS_APPLICATION_SECRET="<Your-Globus-App-Client-Secret>"
# Optional: Restrict access to specific Globus Groups (space-separated UUIDs)
# GLOBUS_GROUPS="<group-uuid-1> <group-uuid-2>"
# Optional: Enforce specific Identity Provider usage (JSON string)
# AUTHORIZED_IDPS='{\"Argonne National Laboratory\": \"411433c5-f79e-40ff-a049-6298917f5600\"}'
# Optional: Enforce Globus high assurance policies (space-separated UUIDs)
# GLOBUS_POLICIES="<policy-uuid-1>"

# --- Database Credentials ---
# Used by Django Gateway, Postgres container, postgres-exporter
POSTGRES_DB="inferencegateway"
POSTGRES_USER="inferencedev"
POSTGRES_PASSWORD="inferencedevpwd" # CHANGE THIS for production
# Hostname: Use "postgres" for Docker-compose networking.
# Use "localhost" for bare-metal if DB is local.
# Use "host.docker.internal" if Gateway runs in Docker but DB runs on the host machine.
PGHOST="postgres"
PGPORT=5432

# --- Redis --- Used for caching, async tasks
# Use "redis" for Docker-compose networking.
# Use "localhost" (or relevant hostname) for bare-metal.
REDIS_URL="redis://redis:6379/0"

# --- Gateway Specific Settings ---
ENABLE_ASYNC="True" # Use the async views (recommended)
MAX_BATCHES_PER_USER=5 # Max concurrent batch jobs allowed per user

# --- Optional: Compute Endpoint Credentials (If needed by specific utils/scripts) ---
# POLARIS_ENDPOINT_ID="<compute-endpoint-app-identity>"
# POLARIS_ENDPOINT_SECRET="<compute-endpoint-add-secret>"

# --- Optional: Grafana Admin Credentials (for Docker setup) ---
# GF_SECURITY_ADMIN_USER=admin
# GF_SECURITY_ADMIN_PASSWORD=admin
```

**Important:** Securely store your `SECRET_KEY` and database credentials, especially in production.

### Initialize Gateway Database

Once the database service is running (either via Docker or manually) and configured in `.env`, initialize the Gateway's database schema.

**Docker:**
```bash
docker-compose -f docker-compose.yml exec inference-gateway python manage.py migrate
```

**Bare Metal:**
```bash
# Ensure DB connection vars are exported in your shell or use python-dotenv
# (e.g., export PGHOST=localhost POSTGRES_USER=...)
python manage.py migrate
```

## Inference Backend Setup (Remote/Local)

This section covers setting up the components on the machine where the AI models will actually run (e.g., an HPC login node, a powerful workstation).

### Install Inference Server (e.g., vLLM)

Choose and install an inference serving framework. vLLM is recommended for performance with many transformer models.

```bash
# Activate the Python environment where you'll run the Globus Compute Endpoint
# (e.g., conda activate my-hpc-env)

# Basic vLLM installation
pip install vllm

# For specific hardware acceleration (CUDA, ROCm), follow official docs:
# https://docs.vllm.ai/en/latest/getting_started/installation.html
```

### Register Globus Compute Functions

The Gateway interacts with the inference server via functions registered with Globus Compute. You need to register at least:

1.  **Inference Function**: Wraps the call to your inference server (e.g., vLLM OpenAI-compatible endpoint).
2.  **Status Function (Optional but Recommended)**: Queries the cluster scheduler (e.g., PBS `qstat`) and node status to aid federated routing.

Navigate to the `compute-functions` directory in your local clone of the repository.

**Important for Local/Non-HPC Setups:** When registering functions or configuring an endpoint you need to explicitly tie the function/endpoint identity back to your registered Globus Application (the Inference Gateway itself). Do this by exporting the *Gateway's* Globus Application Client ID and Secret as environment variables **before** running the registration script or configuring the endpoint:

```bash
# Example using the Gateway's Globus App credentials
export GLOBUS_COMPUTE_CLIENT_ID="<Your-Gateway-Globus-App-Client-ID>"
export GLOBUS_COMPUTE_CLIENT_SECRET="<Your-Gateway-Globus-App-Client-Secret>"

# Example registration command after setting the variables:
# (inference-gateway-py3.11.9-env) ADITYAs-MacBook-Pro-2:compute-functions adityatanikanti$ python3 vllm_register_function.py
# Function registered with UUID - c0b3e315-5294-47be-87e4-d5efd96d524d
# The UUID is stored in vllm_register_function_sophia_multiple_models.txt.
```

Now, register the necessary functions:

```bash
cd path/to/inference-gateway/compute-functions

# Activate the Python environment (e.g., poetry shell or conda activate)

# Register the vLLM inference function (modify the script if needed)
# See compute-functions/vllm_register_function.py
python vllm_register_function.py
# Note the output Function UUID (e.g., <vllm-function-uuid>)

# Register the qstat/status function (modify script for your scheduler if needed)
python qstat_register_function.py
# Note the output Function UUID (e.g., <qstat-function-uuid>)

# (Register other functions like batch processing if needed)
```

**Keep track of the Function UUIDs generated.**

### Configure and Start Globus Compute Endpoint

This endpoint runs on the backend machine, listens for tasks from Globus Compute, and executes the registered functions.

```bash
# Ensure you are in the correct Python environment

# Install the endpoint software (if not already installed)
python3 -m pipx install globus-compute-endpoint

# Configure a new endpoint (follow prompts)
globus-compute-endpoint configure <my-endpoint-name>
# Example: globus-compute-endpoint configure polaris-vllm

# This creates a configuration directory, e.g., ~/.globus_compute/<my-endpoint-name>/
# Edit the config.yaml inside that directory.
```

**Key `config.yaml` settings:**

*   `display_name`: User-friendly name.
*   `funcx_service_address`: Usually `https://compute.api.globus.org`.
*   `multi_user`: Set to `False` typically.
*   `allowed_functions`: **Crucially, add the Function UUIDs** you registered in the previous step here.
*   `environment`: Specify the conda or virtual environment if needed.
*   `worker_init`: Commands to run before starting workers (e.g., `module load PrgEnv-nvhpc cuda; conda activate my-hpc-env`).
*   `provider`: Configure PBSPro, Slurm, Local, etc.
    *   Include scheduler options (`#PBS`, `#SBATCH`), `nodes_per_block`, `walltime`, etc.
*   `strategy`: Configure how tasks are managed.

See [sophia-vllm-config-template.yaml](./compute-endpoints/sophia-vllm-config-template.yaml) for a detailed example.
See [local-vllm-endpoint.yaml](./compute-endpoints/local-vllm-endpoint.yaml) for an example configured for local execution.
Refer to [Globus Compute Endpoint Docs](https://globus-compute.readthedocs.io/en/latest/endpoints.html) for all options.

**After configuring `config.yaml`:**

```bash
# Start the endpoint
globus-compute-endpoint start <my-endpoint-name>

# Note the Endpoint UUID displayed after starting.
```

**Keep track of the Endpoint UUID.**

## Connecting Gateway and Backend

Now, tell the Gateway about the available backend endpoints.

### Update Fixtures

Edit the relevant fixtures file in the Gateway project directory (`inference-gateway/fixtures/`).

*   **For standard, non-federated access:** Edit `endpoints.json`.
*   **For federated access (recommended):** Edit `federated_endpoints.json`.

**Example: `fixtures/federated_endpoints.json`**

```json
[
    {
        "model": "resource_server.federatedendpoint",
        "pk": 1, // Or next available primary key
        "fields": {
            "name": "Meta Llama 3.1 8B Instruct (Federated)",
            "slug": "federated-meta-llama-31-8b-instruct",
            "target_model_name": "meta-llama/Meta-Llama-3.1-8B-Instruct", // Model name users request
            "description": "Federated access point for Llama 3.1 8B Instruct model.",
            "targets": [
                {
                    "cluster": "polaris", // Your cluster name
                    "framework": "vllm",
                    "model": "meta-llama/Meta-Llama-3.1-8B-Instruct", // Model served by this specific target
                    "endpoint_slug": "polaris-vllm-llama-31-8b-instruct", // Unique identifier for this target
                    "endpoint_uuid": "<Endpoint-UUID-from-previous-step>",
                    "function_uuid": "<vllm-function-uuid-from-previous-step>",
                    "api_port": 8000, // Port your vLLM server runs on (within compute node)
                    "allowed_globus_groups": "" // Optional: Restrict this target further
                }
                // Add more targets here for the same model on different clusters/frameworks
            ]
        }
    }
    // Add more FederatedEndpoint entries for other models
]
```

Replace placeholders (`<...>`) with the actual UUIDs and details from the previous steps.

### Load Fixtures

Load the updated fixture file into the Gateway database.

**Docker:**
```bash
docker-compose -f docker-compose.yml exec inference-gateway python manage.py loaddata fixtures/federated_endpoints.json
# Or: docker-compose ... exec inference-gateway python manage.py loaddata fixtures/endpoints.json
```

**Bare Metal:**
```bash
python manage.py loaddata fixtures/federated_endpoints.json
# Or: python manage.py loaddata fixtures/endpoints.json
```

## Starting the Services

### Gateway (Docker or Bare Metal)

**Docker:**
```bash
# Start all services defined in docker-compose.yml (if not already running)
docker-compose -f docker-compose.yml up --build -d
```

**Bare Metal (Development):**
```bash
# Ensure DB connection vars are set
poetry shell
python manage.py runserver 0.0.0.0:8000 # Or your preferred port
```

**Bare Metal (Production with Gunicorn):**
```bash
# Ensure environment variables are set
poetry shell
poetry run gunicorn \
    inference_gateway.asgi:application \
    -k uvicorn.workers.UvicornWorker \
    -b 0.0.0.0:8000 \
    --workers 5 \
    --log-level info
    # Add other Gunicorn flags as needed (--threads, --timeout, log files etc.)
```

### Inference Backend (Globus Compute Endpoint)

Ensure the Globus Compute endpoint is running on the backend machine:

```bash
# On the HPC login node / backend machine
globus-compute-endpoint start <my-endpoint-name>
```

Verify the associated inference server (e.g., vLLM) is started by the endpoint's worker initialization or job submission process.

## Verifying the Setup

Once both the Gateway and at least one Backend Compute Endpoint (with its inference server) are running, you can send a test request. You'll need a valid Globus authentication token.

1.  **Get a Token**: The easiest way might be to log into the Gateway's web interface (if running `runserver` locally: `http://127.0.0.1:8000/`) which uses Globus Auth. After login, you might be able to extract a token from your browser's developer tools (network tab or storage), though this depends on the exact auth flow. Alternatively, use a dedicated Globus SDK script or tool to obtain a token for the registered Globus App Client with the necessary scopes.

2.  **Send Request using cURL**: Replace `<your_globus_token>` and adjust the model name and payload.

    ```bash
    # Example using the federated endpoint for Llama 3.1 8B
    curl -X POST http://127.0.0.1:8000/resource_server/v1/chat/completions \n      -H "Authorization: Bearer <your_globus_token>" \n      -H "Content-Type: application/json" \n      -d '{ 
        "model": "meta-llama/Meta-Llama-3.1-8B-Instruct", 
        "messages": [ 
          {"role": "user", "content": "Explain the concept of Globus Compute in simple terms."} 
        ], 
        "max_tokens": 150 
      }'
    ```

A successful response will be a JSON object containing the model's completion.

## Production Considerations (Nginx)

For production deployments (especially bare-metal), running Django/Gunicorn behind a reverse proxy like Nginx is highly recommended for:

*   **HTTPS/SSL Termination**: Securely handle TLS encryption.
*   **Load Balancing**: Distribute requests across multiple Gunicorn workers/instances.
*   **Serving Static Files**: Efficiently serve CSS, JS, images.
*   **Security**: Add rate limiting, header checks, etc.
*   **Hostname Routing**: Direct traffic based on domain names.

**Example Nginx Configuration Snippet (`/etc/nginx/sites-available/inference_gateway`):**

```nginx
upstream app_server {
    # fail_timeout=0 means we always retry an upstream even if it failed
    # to return a good HTTP response (in case the Gunicorn worker recovers).
    server 127.0.0.1:8000 fail_timeout=0;
    # Add more servers here if running multiple Gunicorn instances
}

server {
    listen 80;
    # listen 443 ssl;
    server_name your-gateway-domain.org;

    # ssl_certificate /path/to/your/cert.pem;
    # ssl_certificate_key /path/to/your/key.pem;

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

*Remember to collect static files (`python manage.py collectstatic`) and configure Nginx to serve them from the specified `alias` path.* 
*Consult Nginx documentation for details on SSL setup and other options.*

## Monitoring

Access the monitoring dashboard (if deployed via Docker Compose with monitoring enabled):

- **Grafana**: http://localhost:3000 (default credentials: admin/admin) - Visualizes metrics.
- **Prometheus**: http://localhost:9090 - Collects metrics.

The Grafana dashboard includes:
- Application metrics (request rates, latency, error rates)
- System metrics (CPU, memory, disk I/O via Node Exporter)
- Database metrics (connection counts, query performance via PostgreSQL Exporter)
- Custom Gateway metrics (inference rates, token counts - requires metrics endpoint in Gateway)

## Troubleshooting

*   **Docker Nginx 404/502/504 Errors:** Verify `nginx_app.conf` mount and upstream definition, check `inference-gateway` logs.
*   **Database Connection Errors:** Check `.env` variables (`PGHOST`, etc.) match context (Docker vs. Host vs. Bare-metal) and firewall/`pg_hba.conf` rules.
*   **Globus Auth Errors**: Ensure Redirect URIs match in Globus Developer portal and `.env` credentials are correct.
*   **Compute Endpoint Issues**: Check endpoint logs (`~/.globus_compute/<endpoint_name>/endpoint.log`) for function execution errors, environment problems, or connection issues.
*   **500 Server Errors on Gateway**: Check gateway logs (`docker-compose logs inference-gateway` or Gunicorn log files) for Python tracebacks.
