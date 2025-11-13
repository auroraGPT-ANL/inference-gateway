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

## Step 2: Configure Environment

Create a `.env` file from the [example environment file](../../../env.example) and customize the `.env` file following the instructions found in the example file:
```bash
cp env.example .env
```

Make sure you include all of the Globus UUIDs and secrets generated during the [Globus setup](../globus-setup/index.md) stage. You can generate the `SECRET_KEY` variable with the following Django command (if installed):
```bash
python -c 'from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())'
```

!!! warning "Production Security"
    For production deployments:
    
    - Set `RUNNING_AUTOMATED_TEST_SUITE=False`
    - Set `DEBUG=False`
    - Use secure passwords and secrets
    - Add your domain to `ALLOWED_HOSTS` or use "*" if appropriate
    - Add at least one Globus High Assurance policy (`GLOBUS_POLICIES`)
    - Set authorized IDP domains (`AUTHORIZED_IDP_DOMAINS`) to match the policy
    - Consider using secrets management (e.g., Docker secrets)

## Step 3: Start the Services

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

## Step 4: Initialize the Database

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

## Step 5: Verify the Gateway

Check that the gateway is running:

```bash
curl http://localhost:8000/
```

Access the Django admin (if superuser was created):

- URL: http://localhost:8000/admin/
- Login with your superuser credentials

## Step 6: Configure Backends

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

