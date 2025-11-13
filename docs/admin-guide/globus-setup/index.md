# Globus Setup

This guide covers creating a Globus project, registering Globus Applications, and optionally creating Globus groups and policies to control access to the service.

## Step 1: Create Globus Project

...

## Step 2: Register Service API Application

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

## Step 3: Register Service Account Application

To handle the communication between the Gateway API and the compute resources (the Inference Backend), you need to create a Globus **Service Account application**. This application represents the Globus identity that will own the Globus Compute endpoints.

1. Visit [developers.globus.org](https://app.globus.org/settings/developers) and sign in.
2. Under **Projects**, click on the project used to register your Service API application from the previous step.
3. Click on **Add an App**.
4. Select **Register a service account ...**.
5. Complete the registration form:
   - Set **App Name** (e.g., "My Inference Endpoints").
   - Set **Privacy Policy** and **Terms & Conditions** URLs if applicable.
6. After registration, a **Client UUID** will be assigned to your Globus application. Generate a **Client Secret** by clicking on the **Add Client Secret** button on the right-hand side. **You will need both for the `.env` configuration.** The UUID will be for `SERVICE_ACCOUNT_ID`, and the secret will be for `SERVICE_ACCOUNT_SECRET`.

## Step 4: Register Public Auth Client

... for the inference auth helper