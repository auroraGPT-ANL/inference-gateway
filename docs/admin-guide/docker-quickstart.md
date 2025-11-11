# Docker Quickstart

This guide spins up the Inference Gateway, PostgreSQL, Redis, and an Nginx reverse proxy with a single `docker compose` command. It is designed for administrators who want a lean, reproducible environment to validate credentials, database connectivity, and backend routing before moving on to Kubernetes or bare-metal deployments.

## Prerequisites

- Docker Desktop 4.29+ (or Docker Engine 24+) with Docker Compose v2
- Globus Service API application (client ID/secret)
- Globus Service Account application for Globus Compute (client ID/secret)
- Access to at least one backend (OpenAI-compatible API or Globus Compute endpoint)
- macOS or Linux host; Windows WSL2 works but paths below assume POSIX shells

## What the Compose Stack Provides

The repository now keeps container assets under `deploy/docker/`:

```
deploy/
  docker/
    Dockerfile             # Builds the Django API image
    nginx.conf             # Minimal reverse proxy config
    .env.example           # Starter environment variables
    fixtures/
      local_vllm_endpoint.json
      federated_example.json
```

Only the required core services run:

- `inference-gateway` (Gunicorn + Django)
- `postgres`
- `redis`
- `nginx`

Monitoring add-ons such as Prometheus, Grafana, and exporters were removed to keep the footprint minimal. Bring your own observability stack when you deploy to production.

## 1. Clone and switch into the repository

```bash
git clone https://github.com/auroraGPT-ANL/inference-gateway.git
cd inference-gateway
```

## 2. Create your `.env`

Copy the starter file and edit it with real credentials:

```bash
cp deploy/docker/.env.example .env
```

Update at least the following keys:

- `SECRET_KEY`: generate with `python -c 'import secrets; print(secrets.token_urlsafe(50))'`
- `GLOBUS_APPLICATION_ID` / `GLOBUS_APPLICATION_SECRET`
- `POLARIS_ENDPOINT_ID` / `POLARIS_ENDPOINT_SECRET`
- `INTERNAL_STREAMING_SECRET`: secret shared with your streaming workers
- `METIS_STATUS_URL` / `METIS_API_TOKENS` if you plan to hit a direct API

> ⚠️ The application always enforces Globus access tokens. Even in Docker you must authenticate through Globus to call the gateway.

## 3. Prepare writable bind mounts

```bash
mkdir -p logs staticfiles
```

`logs` stores Gunicorn output from inside the container. `staticfiles` is populated when you run `collectstatic` later.

## 4. Start the core services

```bash
docker compose up -d --build
```

The first build can take several minutes because Poetry installs all dependencies inside the image.

## 5. Run database migrations

```bash
docker compose run --rm inference-gateway python manage.py migrate
```

You can also create an admin account for the Django admin (optional but handy for inspections):

```bash
docker compose run --rm inference-gateway python manage.py createsuperuser
```

## 6. Collect static assets (optional but recommended for the dashboard)

```bash
docker compose run --rm inference-gateway python manage.py collectstatic --noinput
```

Nginx already mounts `./staticfiles`, so collected assets become available immediately.

## 7. Verify the stack

- Gateway API: http://localhost:8000/
- Django admin: http://localhost:8000/admin/
- Logs: `tail -f logs/backend_gateway.error.log`

Once the API is up, configure backends so the router knows where to send inference requests.

---

## Connecting Backends

The gateway is agnostic to how models are served. This section shows two common options.

### Option A — Direct OpenAI-Compatible API

Use this path when you already operate an HTTPS endpoint that mimics the OpenAI REST contract (official OpenAI, Anthropic-compatible adapters, custom deployments, etc.). The gateway treats these as the `metis` cluster, which relies on a status manifest and per-endpoint API tokens.

1. **Host a status manifest** reachable over HTTP(S). A minimal example:

   ```json
   {
     "openai-gateway": {
       "status": "Live",
       "model": "OpenAI Pass-through",
       "description": "Routes to OpenAI's GPT models",
       "experts": ["openai/gpt-4o-mini"],
       "url": "https://api.openai.com/v1",
       "endpoint_id": "openai-production"
     }
   }
   ```

   Place it somewhere the Docker container can reach. For local testing you can run:

   ```bash
   mkdir -p deploy/docker/examples
   cat > deploy/docker/examples/metis-status.json <<'JSON'
   {
     "openai-gateway": {
       "status": "Live",
       "model": "OpenAI Pass-through",
       "description": "Routes to OpenAI's GPT models",
       "experts": ["openai/gpt-4o-mini"],
       "url": "https://api.openai.com/v1",
       "endpoint_id": "openai-production"
     }
   }
   JSON
   python -m http.server 8055 --directory deploy/docker/examples
   ```

   Then set `METIS_STATUS_URL=http://host.docker.internal:8055/metis-status.json` in `.env` (Docker Desktop exposes the host using `host.docker.internal`).

2. **Provide the API token** in `.env` using the manifest `endpoint_id` as the key:

   ```dotenv
   METIS_API_TOKENS={"openai-production": "sk-your-openai-key"}
   ```

3. Restart the API container to pick up the new environment:

   ```bash
   docker compose up -d inference-gateway
   ```

4. Call the gateway with a Globus access token:

   ```bash
   curl -X POST \
     http://localhost:8000/resource_server/metis/api/v1/chat/completions \
     -H "Authorization: Bearer $MY_GLOBUS_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{
       "model": "openai/gpt-4o-mini",
       "messages": [{"role": "user", "content": "Hello from Docker"}],
       "stream": false
     }'
   ```

The gateway fetches the manifest, validates the model is Live, injects your API key, and forwards the OpenAI payload for you.

### Option B — Globus Compute + vLLM

Use this path when the model executes on an HPC cluster or GPU node exposed through Globus Compute. You need three artefacts: registered Globus function UUID(s), the endpoint UUID, and a Django fixture describing the target.

1. **Register the inference function** from your compute node:

   ```bash
   # On the machine that owns the Globus Compute endpoint
   cd compute-functions
   poetry run python vllm_register_function.py
   ```

   Capture the printed function UUID. Repeat for the status or batch functions if required.

2. **Start (or restart) your Globus Compute endpoint** and note the endpoint UUID:

   ```bash
   globus-compute-endpoint start my-vllm-endpoint
   globus-compute-endpoint list  # to see the endpoint UUID
   ```

3. **Create a minimal endpoint fixture** using the template shipped in the repo:

   ```bash
   cp deploy/docker/fixtures/local_vllm_endpoint.json fixtures/endpoints.json
   ```

   Edit `fixtures/endpoints.json` and replace:

   - `replace-with-endpoint-uuid`
   - `replace-with-function-uuid`
   - Update the `cluster` name if you do not want to use `local`
   - Set `api_port` to the port exposed by your inference server

4. **Load the fixture into the running containers:**

   ```bash
   docker compose run --rm inference-gateway python manage.py loaddata fixtures/endpoints.json
   ```

   Repeat the process for `fixtures/federated_endpoints.json` if you plan to expose federated routing; the starter file lives at `deploy/docker/fixtures/federated_example.json`.

5. **Test the endpoint**:

   ```bash
   curl -X POST \
     http://localhost:8000/resource_server/local/vllm/v1/chat/completions \
     -H "Authorization: Bearer $MY_GLOBUS_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{
       "model": "meta-llama/Meta-Llama-3-8B-Instruct",
       "messages": [{"role": "user", "content": "Status check"}],
       "stream": false
     }'
   ```

If the endpoint is online and your Globus token has the required group membership, the gateway streams responses back in OpenAI format.

---

## Housekeeping

- **Restart services** after environment or fixture changes: `docker compose up -d inference-gateway nginx`
- **Shut everything down** when you are done: `docker compose down`
- **Clean database state** during testing: `docker compose down -v`

You now have a lean Docker deployment that mirrors production authentication and routing behaviour without extra monitoring dependencies. Continue to the Kubernetes or bare-metal guides once you are comfortable with the workflow.
