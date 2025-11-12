# Docker Deployment

This guide shows you how to deploy the FIRST Inference Gateway using Docker and Docker Compose.

## Prerequisites

- Docker Desktop 4.29+ (or Docker Engine 24+) with Docker Compose v2
- Git
- Globus Account and registered applications
- At least 4GB RAM available for containers

## Step 1: Clone the Repository

```bash
git clone https://github.com/auroraGPT-ANL/inference-gateway.git
cd inference-gateway
```

## Step 2: Register Globus Applications

Before deploying, you need to register two Globus applications.

### Service API Application

This handles API authorization:

1. Visit [developers.globus.org](https://app.globus.org/settings/developers)
2. Click **Register a service API**
3. Fill in the form:
   - **App Name**: "My Inference Gateway"
   - **Redirect URIs**: `http://localhost:8000/complete/globus/` (for local development)
   - Add your production URL if deploying to a server
4. Note the **Client UUID** and generate a **Client Secret**

### Add Scope to Service API Application

```bash
export CLIENT_ID="<Your-Service-API-Client-UUID>"
export CLIENT_SECRET="<Your-Service-API-Client-Secret>"

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

Verify the scope:

```bash
curl -s --user $CLIENT_ID:$CLIENT_SECRET https://auth.globus.org/v2/api/clients/$CLIENT_ID
```

### Service Account Application

To handle the communication between the Gateway API and the compute resources (the Inference Backend), you need to create a Globus **Service Account application**. This application represents the Globus identity that will own the Globus Compute endpoints.

1. Visit [developers.globus.org](https://app.globus.org/settings/developers) and sign in.
2. Under **Projects**, click on the project used to register your Service API application from the previous step.
3. Click on **Add an App**.
4. Select **Register a service account ...**.
5. Complete the registration form:
   - Set **App Name** (e.g., "My Inference Endpoints").
   - Set **Privacy Policy** and **Terms & Conditions** URLs if applicable.
6. After registration, a **Client UUID** will be assigned to your Globus application. Generate a **Client Secret** by clicking on the **Add Client Secret** button on the right-hand side. **You will need both for the `.env` configuration.** The UUID will be for `SERVICE_ACCOUNT_ID`, and the secret will be for `SERVICE_ACCOUNT_SECRET`.

## Step 3: Configure Environment

Copy the example environment file:

```bash
cp deploy/docker/env.example .env
```

Edit `.env` with your configuration:

```dotenv
# --- Core Django Settings ---
SECRET_KEY="<generate-with-command-below>"
DEBUG=True
ALLOWED_HOSTS="localhost,127.0.0.1"

# --- Testing/Development Flags ---
# Set to True to skip Globus High Assurance policy checks (for development/testing)
# Set to False for production deployment
RUNNING_AUTOMATED_TEST_SUITE=True
LOG_TO_STDOUT=True  # Makes logs visible via docker-compose logs

# --- Globus Credentials ---
GLOBUS_APPLICATION_ID="<Your-Service-API-Client-UUID>"
GLOBUS_APPLICATION_SECRET="<Your-Service-API-Client-Secret>"
SERVICE_ACCOUNT_ID="<Your-Service-Account-Client-UUID>"
SERVICE_ACCOUNT_SECRET="<Your-Service-Account-Client-Secret>"

# --- Database Credentials (change for production) ---
POSTGRES_DB="inferencegateway"
POSTGRES_USER="inferencedev"
POSTGRES_PASSWORD="change-this-password"
PGHOST="postgres"
PGPORT=5432
PGUSER="inferencedev"
PGPASSWORD="change-this-password"
PGDATABASE="inferencegateway"

# --- Redis ---
REDIS_URL="redis://redis:6379/0"

# --- Gateway Settings ---
MAX_BATCHES_PER_USER=2
STREAMING_SERVER_HOST="localhost:8080"
INTERNAL_STREAMING_SECRET="change-this-secret"
CLI_AUTH_CLIENT_ID="58fdd3bc-e1c3-4ce5-80ea-8d6b87cfb944"
```

Generate a secret key:

```bash
python -c 'from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())'
```

!!! warning "Production Security"
    For production deployments:
    
    - Set `RUNNING_AUTOMATED_TEST_SUITE=False`
    - Change all passwords and secrets
    - Set `DEBUG=False`
    - Add your domain to `ALLOWED_HOSTS`
    - Configure proper Globus policies (`GLOBUS_POLICIES`)
    - Set authorized IDP domains (`AUTHORIZED_IDP_DOMAINS`)
    - Use strong, unique passwords
    - Consider using secrets management (e.g., Docker secrets)

## Step 4: Start the Services

```bash
cd deploy/docker
docker-compose up -d --build
```

This starts:

- `inference-gateway`: The Django API application
- `postgres`: PostgreSQL database
- `redis`: Redis cache
- `nginx`: Reverse proxy (optional, if configured)

Verify services are running:

```bash
docker-compose ps
```

## Step 5: Initialize the Database

Run migrations:

```bash
docker-compose exec inference-gateway python manage.py migrate
```

Create a superuser (optional, for Django admin access):

```bash
docker-compose exec inference-gateway python manage.py createsuperuser
```

Collect static files:

```bash
docker-compose exec inference-gateway python manage.py collectstatic --noinput
```

## Step 6: Verify the Gateway

Check that the gateway is running:

```bash
curl http://localhost:8000/
```

Access the Django admin (if superuser was created):

- URL: http://localhost:8000/admin/
- Login with your superuser credentials

## Step 7: Configure Backends

Now you need to connect inference backends. Choose one:

- [Direct API Connection](../inference-setup/direct-api.md) - Connect to OpenAI or similar APIs
- [Local vLLM](../inference-setup/local-vllm.md) - Run vLLM locally
- [Globus Compute + vLLM](../inference-setup/globus-compute.md) - HPC cluster deployment

## Docker Compose Services

The `docker-compose.yml` includes:

### Core Services

- **inference-gateway**: Django application (port 8000)
- **postgres**: PostgreSQL 15 (port 5432)
- **redis**: Redis 7 (port 6379)

### Optional Services

You can add these to your compose file:

- **nginx**: Reverse proxy for production
- **prometheus**: Metrics collection
- **grafana**: Visualization dashboard

## Common Commands

### View logs

```bash
# All services
docker-compose logs -f

# Specific service
docker-compose logs -f inference-gateway
```

### Restart services

```bash
# All services
docker-compose restart

# Specific service
docker-compose restart inference-gateway
```

### Stop services

```bash
docker-compose down
```

### Stop and remove volumes (clean slate)

```bash
docker-compose down -v
```

### Access container shell

```bash
docker-compose exec inference-gateway /bin/bash
```

### Run Django management commands

```bash
docker-compose exec inference-gateway python manage.py <command>
```

## Updating the Deployment

Pull latest changes:

```bash
git pull origin main
docker-compose build
docker-compose up -d
docker-compose exec inference-gateway python manage.py migrate
```

## Troubleshooting

### Gateway won't start

Check logs:

```bash
docker-compose logs inference-gateway
```

Common issues:

- Missing environment variables
- Database connection failed
- Port 8000 already in use

### Database connection errors

Verify PostgreSQL is running:

```bash
docker-compose ps postgres
```

Check database logs:

```bash
docker-compose logs postgres
```

### Can't access admin panel

Ensure you created a superuser:

```bash
docker-compose exec inference-gateway python manage.py createsuperuser
```

### 502 Bad Gateway from Nginx

Check that the gateway container is running:

```bash
docker-compose ps inference-gateway
```

Verify nginx configuration:

```bash
docker-compose exec nginx nginx -t
```

## Next Steps

- [Configure Inference Backends](../inference-setup/index.md)
- [Production Best Practices](../deployment/production.md)
- [Monitoring Setup](../monitoring.md)

## Additional Resources

- [Docker Compose Documentation](https://docs.docker.com/compose/)
- [Configuration Reference](configuration.md)
- [User Guide](../../user-guide/index.md)

