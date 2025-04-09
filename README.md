[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
![Build](https://github.com/auroraGPT-ANL/inference-gateway/workflows/Django/badge.svg)

# Inference Gateway for FIRST toolkit
A RESTful API Gateway that authenticates and authorizes inference requests to scientific computing clusters. This system enables LLM inference as a service, allowing secure, remote execution of large language models through an OpenAI-compatible API. This the FIRST toolkit's inference gateway.

## System Architecture

![System Architecture](./inference_gateway_architecture_focused.png)

The Inference Gateway consists of several components:
- **API Gateway**: Django-based REST API that handles authentication, authorization, and request routing
- **Globus Auth**: Authentication and authorization service
- **Globus Compute Endpoints**: Remote execution framework on HPC clusters
- **vLLM Backend**: High-performance inference service for LLMs

## Prerequisites

- Python 3.11+
- PostgreSQL
- [Poetry](https://python-poetry.org/docs/#installation)
- Docker and Docker Compose (for containerized deployment)
- Globus Account

## Installation

### Option 1: Docker Deployment (Recommended)

```bash
# Clone the repository
git clone https://github.com/auroraGPT-ANL/inference-gateway.git
cd inference-gateway

# Create .env file (see Configuration section below)
# Ensure you have the required environment variables set.

# Create necessary directories for logs and Prometheus config
mkdir -p logs prometheus

# If you don't have a prometheus.yml, you might need to create a basic one
# echo "global:\\n  scrape_interval: 15s\\nscrape_configs:\\n  - job_name: 'prometheus'\\n    static_configs:\\n      - targets: ['localhost:9090']" > prometheus/prometheus.yml

# Start all services
docker-compose -f docker-compose.yml up --build -d # Run in background

# Initialize the database inside the container (first time setup)
docker-compose -f docker-compose.yml exec inference-gateway python manage.py migrate
docker-compose -f docker-compose.yml exec inference-gateway python manage.py loaddata fixtures/endpoints.json

# Optional: If you have materialized views created by custom commands/migrations:
# docker-compose -f docker-compose.yml exec inference-gateway python manage.py <your_command_to_create/refresh_views>

# Optional: Import data from host DB (if needed, see DB Import section below)
# pg_dump -U <host_user> ... -f dump.dump
# docker cp dump.dump postgres:/tmp/dump.dump
# docker exec -i postgres pg_restore -U dataportaldev -d inferencegateway ... /tmp/dump.dump
```

This will deploy:
- **Inference Gateway API** (required)
- **PostgreSQL database** (required)
- **Prometheus** for metrics collection (optional)
- **Grafana** for metrics visualization (optional)
- **Node Exporter** for system metrics (optional)
- **PostgreSQL Exporter** for database metrics (optional)

**Minimum Requirements:**
- 4 CPU cores
- 8GB RAM
- 20GB storage
- Internet connectivity for Globus authentication

**Recommended:**
- Public IP/domain for external accessibility
- 8 CPU cores
- 16GB RAM
- 100GB SSD storage

### Option 2: Bare Metal Setup / Local Development

```bash
# Clone the repository
git clone https://github.com/auroraGPT-ANL/inference-gateway.git
cd inference-gateway

# Set up Python environment with Poetry
poetry config virtualenvs.in-project true
poetry env use python3.11
poetry install

# Activate the environment
poetry shell

# Ensure database connection environment variables are set in your shell
# (e.g., export PGHOST=localhost PGUSER=... PGPASSWORD=... PGDATABASE=...)
# See the .env example in the Configuration section for variable names.

# Set up the database
python manage.py migrate
python manage.py loaddata fixtures/endpoints.json
# Optional: Create/refresh materialized views needed for the dashboard
# python manage.py <your_command_to_create/refresh_views>
```

## Configuration

Create a [.env](.env) file in the project root with the following parameters. **Note:** This file is used by both bare-metal setup and the Docker Compose deployment.

```text
# Django Core Settings
SECRET_KEY="<some-super-secret-key>"
DEBUG=True # Set to False for production bare-metal, True often helpful for local Docker dev
ALLOWED_HOSTS="localhost,127.0.0.1" # Adjust for your domain/IP if needed

# Globus Credentials (Required for core functionality)
GLOBUS_APPLICATION_ID="<Globus-API-client-identity>"
GLOBUS_APPLICATION_SECRET="<Globus-API-client-secret>"
GLOBUS_GROUPS="
<globus-group-uuid-1>
<globus-group-uuid-2>
"
GLOBUS_POLICIES="
<globus-policy-uuid-1>
"
AUTHORIZED_IDPS='
{
    "<identity-provider-name-1>" : "<identity-provider-uuid-1>"
}
'

# Compute Endpoint IDs (Replace with actual IDs)
POLARIS_ENDPOINT_ID="<compute-endpoint-app-identity>"
POLARIS_ENDPOINT_SECRET="<compute-endpoint-add-secret>"

# Database Credentials (Used by inference-gateway, postgres, postgres-exporter)
POSTGRES_DB="inferencegateway" # Database name
POSTGRES_USER="dataportaldev" # Database user
POSTGRES_PASSWORD="dataportaldevpwd123" # Database password
PGHOST="postgres" # Service name in docker-compose, or "localhost" for bare-metal
# Use PGHOST="host.docker.internal" to connect from Docker to a DB running on your host machine
# (Requires host DB to allow connections from Docker's IP range, see PostgreSQL docs)
PGPORT=5432

# Redis URL (Used by inference-gateway)
REDIS_URL="redis://redis:6379/0" # Service name in docker-compose

# Gateway Specific Settings
ENABLE_ASYNC="True"
MAX_BATCHES_PER_USER=1

# Grafana Admin Credentials (Used by grafana service in docker-compose)
GF_SECURITY_ADMIN_USER=admin
GF_SECURITY_ADMIN_PASSWORD=admin
# Optional: GF_USERS_ALLOW_SIGN_UP=false

# Django Settings Module (Defaults usually fine)
# DJANGO_SETTINGS_MODULE=inference_gateway.settings
```

## Obtaining Globus API Credentials

To get the required Globus credentials:

1. Visit [developers.globus.org](https://developers.globus.org) and sign in
2. Select **Register a new application**
3. Choose **Portal / Science Gateway** as the application type
4. Create a new project or select an existing one
5. Complete the registration form:
    - Set **App Name** for display on Globus login pages
    - Add **Redirects** for your domain
    - Configure **Scopes** for required permissions
    - Set **Privacy Policy** and **Terms & Conditions** URLs
6. After registration, you'll receive:
   - `GLOBUS_APPLICATION_ID` (Client ID)
   - `GLOBUS_APPLICATION_SECRET` (Client Secret)
7. For advanced configurations:
   - `GLOBUS_GROUPS` are UUIDs of groups authorized to use the service
   - `GLOBUS_POLICIES` are high assurance policies that enforce identity provider checks
   - `AUTHORIZED_IDPS` provides additional authorization checks in the API

## Setting up the Compute Endpoints

Globus Compute Endpoints allow remote execution on HPC clusters. These typically run on login nodes. You can set it up locally for testing purposes if you have enough resources to run the vLLM server.

### Installation
```bash
# Install the endpoint software
python3 -m pipx install globus-compute-endpoint

# Configure the endpoint
globus-compute-endpoint configure

# Start the endpoint
globus-compute-endpoint start <ENDPOINT_NAME>
```

This will generate an endpoint ID that you'll need later.

### Globus Compute Endpoint Configuration

The compute-endpoints folder contains templates for various model configurations. See [sophia-vllm-config-template.yaml](./compute-endpoints/sophia-vllm-config-template.yaml) for an example configuration.

For detailed configuration options, see the [Globus Compute documentation](https://globus-compute.readthedocs.io/en/3.5.0/).

### Registering Inference Functions

The [compute-functions folder](./compute-functions) contains example functions that can be registered with Globus Compute. See [vllm_register_function.py](./compute-functions/vllm_register_function.py) for an example function.

Ensure that the registered function UUID is in the allowed_functions list in the compute endpoint configuration file. Also it should be registered within the same conda environment as the compute endpoint.

### Registering qstat function.

This is optional but recommended on HPC clusters. This function is used to get the status of the model running on the compute nodes. See [qstat_register_function.py](./compute-functions/qstat_register_function.py) for an example function. 



## Setting Up vLLM

vLLM is a high-performance inference engine for LLMs. Although and inference serving framework can be used we have found vLLM to be the most performant and simple to set up. Installation instructions vary by system:

```bash
# Basic installation with pip
pip install vllm

# For specific accelerators (CUDA, ROCm, etc.), see:
# https://docs.vllm.ai/en/latest/getting_started/installation.html
```

The compute endpoint configurations include startup scripts for vLLM server.

## Adding Models to the Gateway

Update [fixtures/endpoints.json](./fixtures/endpoints.json) with your endpoint and function information:

```json
# Example of adding a new model to the gateway
[
  {
        "model": "resource_server.endpoint",
        "pk": 1,
        "fields": {
            "endpoint_slug": "sophia-vllm-qwenqwq-32b",
            "cluster": "sophia",
            "framework": "vllm",
            "model": "Qwen/QwQ-32B",
            "api_port": 8000,
            "endpoint_uuid": "<endpoint-uuid-from-globus-compute>",
            "function_uuid": "<function-uuid-from-globus-compute>",
            "batch_endpoint_uuid": "<batch-endpoint-uuid-from-globus-compute>",
            "batch_function_uuid": "<batch-function-uuid-from-globus-compute>"
        }
    }
]
```

Reload the fixtures:

```bash
python manage.py loaddata fixtures/endpoints.json
```

## Running the Server

### Development

```bash
# Ensure DB connection vars (PGHOST, POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB)
# are set in your shell environment for your local DB (e.g., using export).
# Alternatively, add python-dotenv and load a specific .env file in manage.py.
# Example using export for a local DB:
# export PGHOST=localhost POSTGRES_USER=mylocaluser POSTGRES_DB=mylocaldb POSTGRES_PASSWORD=xxx
python manage.py runserver
```

### Production (with Gunicorn)

```bash
# NOTE: The gunicorn_asgi.config.py file uses a custom worker that may cause issues.
# The recommended approach, especially in Docker, is to run Gunicorn with explicit parameters:
# Ensure environment variables (DB connection etc.) are set appropriately first.
poetry run gunicorn \
    inference_gateway.asgi:application \
    -k uvicorn.workers.UvicornWorker \
    -b 0.0.0.0:7000 \
    --workers 5 \
    --threads 4 \
    --timeout 1800 \
    --log-level info \
    --access-logfile /path/to/access.log \
    --error-logfile /path/to/error.log \
    --capture-output

# Adjust workers, log paths, and other parameters as needed for your bare-metal setup.
```

### Docker

```bash
# Ensure .env file is configured for docker deployment (e.g., DEBUG=True, PGHOST=postgres, REDIS_URL=redis://redis:6379/0)
# Create necessary directories first (see Docker Deployment section)
docker-compose -f docker-compose.yml up --build
```

## Monitoring

Access the monitoring dashboard at:
- **Dashboard**: http://localhost:8000/dashboard/analytics
- **Grafana**: http://localhost:3000 (default credentials: admin/admin)
- **Prometheus**: http://localhost:9090

The **/dashboard/analytics** page requires specific PostgreSQL materialized views (e.g., `mv_overall_stats`, `mv_model_requests`). Ensure these are created and refreshed according to the steps in the Installation section or your project's specific procedures.

The dashboard includes:
- Application metrics (request rates, latency, error rates)
- System metrics (CPU, memory, disk I/O)
- Database metrics (connection counts, query performance)
- Inference request rates
- Batch request rates
- Token processing rates
- Error rates
- Latency metrics
- Active user counts

## Troubleshooting

*   **Nginx 404/502/504 Errors:**
    *   Verify `docker-compose.yml` correctly mounts `./nginx_app.conf` to `/etc/nginx/conf.d/default.conf`.
    *   Check `nginx_app.conf` has the correct `upstream app_server` definition (pointing to `inference-gateway:7000`).
    *   Check `inference-gateway` service logs (`docker-compose logs inference-gateway`) for startup errors.
    *   Ensure `PGHOST` in `.env` is correct for the context (`postgres` for Docker-to-Docker, `host.docker.internal` for Docker-to-Host).
*   **Database Connection Errors (e.g., Password Auth Failed, Timeout, Connection Refused):**
    *   **Docker:** Ensure `.env` variables (`POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `PGHOST`, `PGPORT`) are correctly set and match the expectations in `settings.py`.
    *   **Local `runserver`:** Ensure the same variables are correctly exported in your *local shell* environment and point to the intended database (local host or Docker via mapped port), or use `python-dotenv`.
    *   **Docker-to-Host:** If using `PGHOST=host.docker.internal`, verify the host PostgreSQL allows network connections and `pg_hba.conf` permits connections from Docker IPs.
*   **Dashboard (`/dashboard/analytics`) 500 Error or Missing Data:**
    *   Check `inference-gateway` logs (`docker-compose logs inference-gateway`) for Python tracebacks when accessing `/dashboard/metrics`.
    *   Verify that the required materialized views (e.g., `mv_overall_stats`) exist in the target database and are populated. Check if creation/refresh commands ran successfully during setup.