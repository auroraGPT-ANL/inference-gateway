[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
![Build](https://github.com/auroraGPT-ANL/inference-gateway/workflows/Django/badge.svg)

# Inference Gateway for FIRST toolkit
A RESTful API Gateway that authenticates and authorizes inference requests to scientific computing clusters. This system enables LLM inference as a service, allowing secure, remote execution of large language models through an OpenAI-compatible API.

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

# Create .env file (see Configuration section)
# ...

# Start all services
docker-compose -f docker-compose.yml up --build
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

# Set up the database
python manage.py migrate
python manage.py loaddata fixtures/endpoints.json
```

## Configuration

Create a [.env](.env) file in the project root with the following parameters:

```text
SECRET_KEY="<some-super-secret-key>"
GLOBUS_APPLICATION_ID="<Globus-API-client-identity>"
GLOBUS_APPLICATION_SECRET="<Globus-API-client-secret>"
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
PGPASSWORD="<Postgres Password>"
ENABLE_ASYNC="True"
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
python manage.py runserver
```

### Production (with Gunicorn)

```bash
poetry run gunicorn inference_gateway.wsgi:application --config gunicorn_asgi.config.py
```

## Monitoring

Access the monitoring dashboard at:
- **Dashboard**: http://localhost:8000/dashboard/analytics
- **Grafana**: http://localhost:3000 (default credentials: admin/admin)
- **Prometheus**: http://localhost:9090

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