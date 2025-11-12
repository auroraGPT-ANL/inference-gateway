# Bare Metal Setup

This guide covers installing the FIRST Inference Gateway directly on your server without Docker.

## Prerequisites

- Linux server (Ubuntu 20.04+, CentOS 8+, or similar)
- Python 3.12 or later
- PostgreSQL 13 or later
- Redis 6 or later
- Poetry (Python dependency manager)
- Sudo access for system packages
- At least 4GB RAM

## Step 1: Install System Dependencies

### Ubuntu/Debian

```bash
sudo apt update
sudo apt install -y python3.12 python3.12-dev python3.12-venv \
    postgresql postgresql-contrib redis-server \
    build-essential libpq-dev git curl
```

### CentOS/RHEL

```bash
sudo dnf install -y python3.12 python3.12-devel \
    postgresql postgresql-server redis \
    gcc gcc-c++ make libpq-devel git
```

## Step 2: Install Poetry

```bash
curl -sSL https://install.python-poetry.org | python3 -

# Add to PATH
export PATH="$HOME/.local/bin:$PATH"

# Verify installation
poetry --version
```

## Step 3: Clone and Setup Project

```bash
git clone https://github.com/auroraGPT-ANL/inference-gateway.git
cd inference-gateway

# Configure Poetry to create venv in project
poetry config virtualenvs.in-project true

# Set Python version
poetry env use python3.12

# Install dependencies
poetry install

# Activate environment
poetry shell
```

## Step 4: Configure PostgreSQL

### Initialize PostgreSQL (if first time)

```bash
# Ubuntu/Debian (usually auto-initialized)
sudo systemctl start postgresql
sudo systemctl enable postgresql

# CentOS/RHEL
sudo postgresql-setup --initdb
sudo systemctl start postgresql
sudo systemctl enable postgresql
```

### Create Database and User

```bash
sudo -u postgres psql

# In PostgreSQL shell:
CREATE DATABASE inferencegateway;
CREATE USER inferencedev WITH PASSWORD 'your-secure-password';
ALTER ROLE inferencedev SET client_encoding TO 'utf8';
ALTER ROLE inferencedev SET default_transaction_isolation TO 'read committed';
ALTER ROLE inferencedev SET timezone TO 'UTC';
GRANT ALL PRIVILEGES ON DATABASE inferencegateway TO inferencedev;
\q
```

### Configure PostgreSQL Authentication

Edit `/etc/postgresql/*/main/pg_hba.conf` (path may vary):

```
# Add this line (adjust for your security needs)
host    inferencegateway    inferencedev    127.0.0.1/32    md5
```

Restart PostgreSQL:

```bash
sudo systemctl restart postgresql
```

## Step 5: Configure Redis

Start and enable Redis:

```bash
sudo systemctl start redis
sudo systemctl enable redis

# Verify it's running
redis-cli ping
# Should return: PONG
```

## Step 6: Register Globus Applications

Follow the same steps as in the Docker guide:

### Service API Application

1. Visit [developers.globus.org](https://app.globus.org/settings/developers)
2. Register a **service API application**
3. Add redirect URI: `http://your-server-ip:8000/complete/globus/`
4. Note Client UUID and Secret

### Add Scope

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

### Service Account Application

1. In the same project, register a **service account application**
2. Note Client UUID and Secret

## Step 7: Configure Environment

Create `.env` file in project root:

```bash
cat > .env << 'EOF'
# --- Core Django Settings ---
SECRET_KEY="<generate-with-command-below>"
DEBUG=False
ALLOWED_HOSTS="your-server-ip,your-domain.com"

# --- Globus Credentials ---
GLOBUS_APPLICATION_ID="<Your-Service-API-Client-UUID>"
GLOBUS_APPLICATION_SECRET="<Your-Service-API-Client-Secret>"
SERVICE_ACCOUNT_ID="<Your-Service-Account-Client-UUID>"
SERVICE_ACCOUNT_SECRET="<Your-Service-Account-Client-Secret>"

# --- Database Credentials ---
POSTGRES_DB="inferencegateway"
POSTGRES_USER="inferencedev"
POSTGRES_PASSWORD="your-secure-password"
PGHOST="localhost"
PGPORT=5432
PGUSER="inferencedev"
PGPASSWORD="your-secure-password"
PGDATABASE="inferencegateway"

# --- Redis ---
REDIS_URL="redis://localhost:6379/0"

# --- Gateway Settings ---
MAX_BATCHES_PER_USER=2
STREAMING_SERVER_HOST="localhost:8080"
INTERNAL_STREAMING_SECRET="<generate-random-secret>"
CLI_AUTH_CLIENT_ID="58fdd3bc-e1c3-4ce5-80ea-8d6b87cfb944"
EOF
```

Generate secret key:

```bash
python -c 'from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())'
```

## Step 8: Initialize Database

```bash
# Make sure you're in the poetry shell
poetry shell

# Run migrations
python manage.py makemigrations
python manage.py migrate

# Create superuser
python manage.py createsuperuser

# Collect static files
python manage.py collectstatic --noinput
```

## Step 9: Test the Gateway

Run development server:

```bash
python manage.py runserver 0.0.0.0:8000
```

Test in another terminal:

```bash
curl http://localhost:8000/
```

## Step 10: Setup Production Server (Gunicorn)

### Install Gunicorn (already included in poetry dependencies)

Create a systemd service file:

```bash
sudo nano /etc/systemd/system/inference-gateway.service
```

Add the following:

```ini
[Unit]
Description=FIRST Inference Gateway
After=network.target postgresql.service redis.service

[Service]
Type=notify
User=your-username
Group=your-username
WorkingDirectory=/path/to/inference-gateway
Environment="PATH=/path/to/inference-gateway/.venv/bin"
EnvironmentFile=/path/to/inference-gateway/.env
ExecStart=/path/to/inference-gateway/.venv/bin/gunicorn \
    inference_gateway.asgi:application \
    -k uvicorn.workers.UvicornWorker \
    -b 0.0.0.0:8000 \
    --workers 4 \
    --log-level info \
    --access-logfile /path/to/inference-gateway/logs/access.log \
    --error-logfile /path/to/inference-gateway/logs/error.log

[Install]
WantedBy=multi-user.target
```

### Start and Enable Service

```bash
# Create logs directory
mkdir -p logs

# Reload systemd
sudo systemctl daemon-reload

# Start service
sudo systemctl start inference-gateway

# Enable on boot
sudo systemctl enable inference-gateway

# Check status
sudo systemctl status inference-gateway
```

## Step 11: Setup Nginx (Recommended)

### Install Nginx

```bash
# Ubuntu/Debian
sudo apt install nginx

# CentOS/RHEL
sudo dnf install nginx
```

### Configure Nginx

Create site configuration:

```bash
sudo nano /etc/nginx/sites-available/inference-gateway
```

Add the following:

```nginx
upstream inference_gateway {
    server 127.0.0.1:8000 fail_timeout=0;
}

server {
    listen 80;
    server_name your-domain.com;
    client_max_body_size 100M;

    location /static/ {
        alias /path/to/inference-gateway/staticfiles/;
    }

    location / {
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Host $http_host;
        proxy_redirect off;
        proxy_buffering off;
        proxy_pass http://inference_gateway;
    }
}
```

Enable the site:

```bash
# Ubuntu/Debian
sudo ln -s /etc/nginx/sites-available/inference-gateway /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl restart nginx

# CentOS/RHEL
sudo ln -s /etc/nginx/sites-available/inference-gateway /etc/nginx/conf.d/
sudo nginx -t
sudo systemctl restart nginx
```

### Setup SSL with Let's Encrypt

```bash
# Ubuntu/Debian
sudo apt install certbot python3-certbot-nginx

# CentOS/RHEL
sudo dnf install certbot python3-certbot-nginx

# Get certificate
sudo certbot --nginx -d your-domain.com
```

## Step 12: Configure Firewall

```bash
# Ubuntu/Debian (UFW)
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable

# CentOS/RHEL (firewalld)
sudo firewall-cmd --permanent --add-service=http
sudo firewall-cmd --permanent --add-service=https
sudo firewall-cmd --reload
```

## Maintenance

### View Logs

```bash
# Application logs
tail -f logs/error.log
tail -f logs/access.log

# System service logs
sudo journalctl -u inference-gateway -f
```

### Restart Service

```bash
sudo systemctl restart inference-gateway
```

### Update Application

```bash
cd /path/to/inference-gateway
git pull origin main
poetry install
python manage.py migrate
python manage.py collectstatic --noinput
sudo systemctl restart inference-gateway
```

## Troubleshooting

### Service won't start

Check logs:

```bash
sudo journalctl -u inference-gateway -n 50
```

Check configuration:

```bash
poetry shell
python manage.py check
```

### Database connection errors

Verify PostgreSQL is running:

```bash
sudo systemctl status postgresql
```

Test connection:

```bash
psql -h localhost -U inferencedev -d inferencegateway
```

### Permission errors

Ensure the service user owns the files:

```bash
sudo chown -R your-username:your-username /path/to/inference-gateway
```

### Nginx errors

Check nginx error log:

```bash
sudo tail -f /var/log/nginx/error.log
```

Test configuration:

```bash
sudo nginx -t
```

## Next Steps

- [Configure Inference Backends](../inference-setup/index.md)
- [Production Best Practices](../deployment/production.md)
- [Monitoring Setup](../monitoring.md)

## Additional Resources

- [Configuration Reference](configuration.md)
- [Gunicorn Documentation](https://docs.gunicorn.org/)
- [Nginx Documentation](https://nginx.org/en/docs/)

