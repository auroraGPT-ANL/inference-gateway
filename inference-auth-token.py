import globus_sdk
from globus_sdk.login_flows import LocalServerLoginFlowManager # Needed to access globus_sdk.gare
import os
from dotenv import load_dotenv
import json # For parsing domains list

# Load .env variables
load_dotenv()

# Globus UserApp name (can remain constant or be made configurable if needed)
APP_NAME = os.getenv("CLI_APP_NAME", "inference_auth_cli_app")

# Public client ID for this CLI authentication flow (MUST be set in .env)
CLI_AUTH_CLIENT_ID = os.getenv("CLI_AUTH_CLIENT_ID")
if not CLI_AUTH_CLIENT_ID:
    raise ValueError("Error: CLI_AUTH_CLIENT_ID must be set in the .env file.")

# Inference gateway Application Client ID (MUST be set in .env)
GATEWAY_CLIENT_ID = os.getenv("GLOBUS_APPLICATION_ID")
if not GATEWAY_CLIENT_ID:
    raise ValueError("Error: GLOBUS_APPLICATION_ID must be set in the .env file (this is the Gateway's Client ID).")

GATEWAY_SCOPE = f"https://auth.globus.org/scopes/{GATEWAY_CLIENT_ID}/action_all"

# Path where access and refresh tokens are stored
TOKENS_DIR = os.path.expanduser(os.getenv("CLI_TOKEN_DIR", f"~/.globus/app/{CLI_AUTH_CLIENT_ID}/{APP_NAME}"))
TOKENS_PATH = os.path.join(TOKENS_DIR, "tokens.json")

# Ensure the token directory exists
os.makedirs(TOKENS_DIR, exist_ok=True)

# Allowed identity provider domains (optional, comma-separated string in .env)
# Example: CLI_ALLOWED_DOMAINS="anl.gov,alcf.anl.gov"
allowed_domains_str = os.getenv("CLI_ALLOWED_DOMAINS", "")
ALLOWED_DOMAINS = [domain.strip() for domain in allowed_domains_str.split(',') if domain.strip()]

# Globus authorizer parameters
# Use session_required_single_domain if specific domains are provided
if ALLOWED_DOMAINS:
    GA_PARAMS = globus_sdk.gare.GlobusAuthorizationParameters(session_required_single_domain=ALLOWED_DOMAINS)
    print(f"INFO: Restricting login to domains: {ALLOWED_DOMAINS}")
else:
    GA_PARAMS = None # No domain restriction
    print("INFO: No domain restrictions specified for login.")


# Error handler to guide user through specific identity providers
class DomainBasedErrorHandler:
    def __call__(self, app, error):
        print(f"Encountered error '{error}', initiating login...")
        # Pass GA_PARAMS which might contain domain restrictions
        app.login(auth_params=GA_PARAMS)


# Get refresh authorizer object
def get_auth_object(force=False):
    """
    Create a Globus UserApp with the inference service scope
    and trigger the authentication process. If authentication
    has already happened, existing tokens will be reused unless force=True.
    """

    # Create Globus user application
    app = globus_sdk.UserApp(
        APP_NAME,
        client_id=CLI_AUTH_CLIENT_ID,
        scope_requirements={GATEWAY_CLIENT_ID: [GATEWAY_SCOPE]},
        config=globus_sdk.GlobusAppConfig(
            request_refresh_tokens=True,
            token_storage=globus_sdk.FileTokenStorage(TOKENS_PATH), # Use specified path
            token_validation_error_handler=DomainBasedErrorHandler()
        ),
    )

    # Force re-login if required
    if force:
        # Clear existing tokens before forcing login
        if os.path.exists(TOKENS_PATH):
            print(f"INFO: Clearing existing tokens at {TOKENS_PATH} due to --force flag.")
            os.remove(TOKENS_PATH)
        print("INFO: Forcing new login...")
        app.login(auth_params=GA_PARAMS) # Pass GA_PARAMS

    # Authenticate using your Globus account or reuse existing tokens
    # get_authorizer should now load from/save to TOKENS_PATH via FileTokenStorage
    auth = app.get_authorizer(GATEWAY_CLIENT_ID)

    # Return the Globus refresh token authorizer
    return auth


# Get access token
def get_access_token():
    """
    Load existing tokens, refresh the access token if necessary,
    and return the valid access token. If there is no token stored
    in the home directory, or if the refresh token is expired following
    inactivity, an authentication will be triggered.
    """
    # Get authorizer object and authenticate if needed (will load/save tokens)
    auth = get_auth_object() # force=False by default

    # Make sure the stored access token if valid, and refresh otherwise
    try:
        auth.ensure_valid_token()
    except globus_sdk.AuthAPIError as e:
        # Handle specific errors like invalid refresh token if needed
        print(f"Error ensuring token validity: {e}")
        print("Attempting re-authentication...")
        auth = get_auth_object(force=True) # Force re-auth if refresh fails
        auth.ensure_valid_token() # Try again

    # Return the access token
    return auth.access_token


# If this file is executed as a script ...
if __name__ == "__main__":

    # Imports
    import argparse

    # Exception to raise in case of errors
    class InferenceAuthError(Exception):
        pass

    # Constant
    AUTHENTICATE_ACTION = "authenticate"
    GET_ACCESS_TOKEN_ACTION = "get_access_token"

    # Define possible arguments
    parser = argparse.ArgumentParser(description="Authenticate with Globus and get access tokens for the Inference Gateway.")
    parser.add_argument('action', choices=[AUTHENTICATE_ACTION, GET_ACCESS_TOKEN_ACTION], help="Action to perform: 'authenticate' or 'get_access_token'.")
    parser.add_argument("-f", "--force", action="store_true", help="Force re-authentication, ignoring existing tokens.")
    args = parser.parse_args()

    # Authentication
    if args.action == AUTHENTICATE_ACTION:
        print("Attempting authentication...")
        # Authenticate using Globus account, potentially forcing re-login
        try:
            auth_obj = get_auth_object(force=args.force)
            # Verify token can be retrieved after authentication
            if auth_obj.access_token:
                 print(f"Authentication successful. Tokens stored/updated in {TOKENS_PATH}")
            else:
                 print("Authentication process completed, but failed to retrieve token immediately. Try 'get_access_token'.")
        except Exception as e:
            print(f"Authentication failed: {e}")
            raise InferenceAuthError(f"Authentication process failed. Details: {e}")


    # Get token
    elif args.action == GET_ACCESS_TOKEN_ACTION:
        # Make sure no force flag was provided with get_access_token
        if args.force:
            # Suggest using authenticate --force instead
            parser.error("The --force flag should be used with the 'authenticate' action, not 'get_access_token'. To force re-authentication, run: python inference_auth_token.py authenticate --force")

        try:
            # Check if tokens exist before trying to get one without auth flow
            if not os.path.isfile(TOKENS_PATH):
                 print('Access token file not found. Please authenticate first by running:')
                 print('python inference_auth_token.py authenticate')
                 # Optionally, trigger authentication directly here?
                 # print("Attempting authentication now...")
                 # auth_obj = get_auth_object(force=False)
                 # if auth_obj.access_token:
                 #    print(auth_obj.access_token)
                 # else:
                 #    raise InferenceAuthError("Authentication required, but failed to get token.")
                 exit(1) # Exit if no tokens and we don't auto-auth

            # Load tokens, refresh token if necessary, and print access token
            access_token = get_access_token()
            print(access_token)
        except Exception as e:
            print(f"Failed to get access token: {e}")
            print("You might need to re-authenticate:")
            print("python inference_auth_token.py authenticate --force")
            raise InferenceAuthError(f"Failed to retrieve access token. Details: {e}")