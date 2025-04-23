import globus_sdk
from globus_sdk.login_flows import LocalServerLoginFlowManager # Needed to access globus_sdk.gare
import os
from dotenv import load_dotenv
import json # For parsing domains list
import sys # Import sys to redirect print output

# Load .env variables first
load_dotenv()

# --- Configuration ---
# Globus UserApp name (can remain constant or be made configurable if needed via .env)
APP_NAME = os.getenv("CLI_APP_NAME", "inference_app") # Default to original name

# Public client ID for this CLI authentication flow (reads from .env, falls back to original default)
CLI_AUTH_CLIENT_ID = os.getenv("CLI_AUTH_CLIENT_ID", "58fdd3bc-e1c3-4ce5-80ea-8d6b87cfb944")

# Inference gateway Application Client ID (reads from .env, MUST BE SET)
GATEWAY_CLIENT_ID = os.getenv("GLOBUS_APPLICATION_ID")
if not GATEWAY_CLIENT_ID:
    # Provide a more specific default or raise error if critical
    # For now, using the original hardcoded value as a fallback placeholder,
    # but users should really set GLOBUS_APPLICATION_ID in .env
    print("WARNING: GLOBUS_APPLICATION_ID not found in .env. Using fallback value. Please set it in .env.", file=sys.stderr) # To stderr
    GATEWAY_CLIENT_ID = os.getenv("GLOBUS_APPLICATION_ID", "681c10cc-f684-4540-bcd7-0b4df3bc26ef")

GATEWAY_SCOPE = f"https://auth.globus.org/scopes/{GATEWAY_CLIENT_ID}/action_all"

# Path where access and refresh tokens are stored (uses original logic based on CLI_AUTH_CLIENT_ID and APP_NAME)
# Ensure the parent directory exists
TOKENS_DIR = os.path.expanduser(f"~/.globus/app/{CLI_AUTH_CLIENT_ID}/{APP_NAME}")
os.makedirs(TOKENS_DIR, exist_ok=True) # Create dir if it doesn't exist
TOKENS_PATH = os.path.join(TOKENS_DIR, "tokens.json") # Original path construction


# Allowed identity provider domains (reads from .env, falls back to original default)
# Example: CLI_ALLOWED_DOMAINS="anl.gov,alcf.anl.gov"
allowed_domains_str = os.getenv("CLI_ALLOWED_DOMAINS", "anl.gov,alcf.anl.gov") # Default to original list
ALLOWED_DOMAINS = [domain.strip() for domain in allowed_domains_str.split(',') if domain.strip()]

# Globus authorizer parameters to point to specific identity providers
if ALLOWED_DOMAINS:
    GA_PARAMS = globus_sdk.gare.GlobusAuthorizationParameters(session_required_single_domain=ALLOWED_DOMAINS)
else:
    GA_PARAMS = None
    print("INFO: No domain restrictions specified for login.", file=sys.stderr) # To stderr


# --- Original Logic ---

# Error handler to guide user through specific identity providers
class DomainBasedErrorHandler:
    def __call__(self, app, error):
        print(f"Encountered error '{error}', initiating login...", file=sys.stderr) # To stderr
        app.login(auth_params=GA_PARAMS)


# Get refresh authorizer object
def get_auth_object(force=False):
    """
    Create a Globus UserApp with the inference service scope
    and trigger the authentication process. If authentication
    has already happened, existing tokens will be reused.
    Uses default token storage mechanisms.
    """

    # Create Globus user application
    # NOTE: Not specifying token_storage uses the default behavior which
    # should align with the standard path defined in TOKENS_PATH.
    app = globus_sdk.UserApp(
        APP_NAME,
        client_id=CLI_AUTH_CLIENT_ID,
        scope_requirements={GATEWAY_CLIENT_ID: [GATEWAY_SCOPE]},
        config=globus_sdk.GlobusAppConfig(
            request_refresh_tokens=True,
            token_validation_error_handler=DomainBasedErrorHandler()
        ),
    )

    # Force re-login if required
    if force:
        # Original script just calls login, which handles clearing old tokens internally
        print("INFO: Forcing new login (clearing existing tokens implicitly)...", file=sys.stderr) # To stderr
        app.login(auth_params=GA_PARAMS)

    # Authenticate using your Globus account or reuse existing tokens
    auth = app.get_authorizer(GATEWAY_CLIENT_ID)

    # Return the Globus refresh token authorizer
    return auth


# Get access token
def get_access_token():
    """
    Load existing tokens, refresh the access token if necessary,
    and return the valid access token. If there is no token stored
    in the default location, or if the refresh token is expired following
    inactivity, an authentication will be triggered.
    """

    # Get authorizer object and authenticate if need be
    auth = get_auth_object() # force=False

    # Make sure the stored access token if valid, and refresh otherwise
    try:
        auth.ensure_valid_token()
    except globus_sdk.AuthAPIError as e:
        print(f"Error ensuring token validity: {e}", file=sys.stderr) # To stderr
        print("Attempting re-authentication...", file=sys.stderr) # To stderr
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
        print("Attempting authentication...", file=sys.stderr) # To stderr
        try:
            # Authenticate using Globus account (original logic)
            _ = get_auth_object(force=args.force)
            # Info message to stderr
            print(f"Authentication successful. Tokens stored/updated in default location derived from Client ID and App Name (likely near {TOKENS_PATH}).", file=sys.stderr)
        except Exception as e:
             # Error message to stderr
             print(f"Authentication failed: {e}", file=sys.stderr)
             raise InferenceAuthError(f"Authentication process failed. Details: {e}")

    # Get token
    elif args.action == GET_ACCESS_TOKEN_ACTION:

        # Make sure tokens exist (original check based on constructed path)
        if not os.path.isfile(TOKENS_PATH):
             # Error message to stderr
             print('Access token file not found at expected location. Please authenticate first by running:', file=sys.stderr)
             print('python inference-auth_token.py authenticate', file=sys.stderr)
             exit(1)

        # Make sure no force flag was provided (original check)
        if args.force:
             # argparse handles printing error to stderr and exiting
             parser.error("The --force flag should be used with the 'authenticate' action, not 'get_access_token'. To force re-authentication, run: python inference_auth_token.py authenticate --force")

        try:
            # Load tokens, refresh token if necessary, and print access token
            access_token = get_access_token()
            print(access_token) # <<< Print the token itself to STDOUT
        except Exception as e:
            # Error message to stderr
            print(f"Failed to get access token: {e}", file=sys.stderr)
            print("You might need to re-authenticate:", file=sys.stderr)
            print("python inference_auth_token.py authenticate --force", file=sys.stderr)
            raise InferenceAuthError(f"Failed to retrieve access token. Details: {e}")