# Django imports
from django.conf import settings

# Globus imports
import globus_sdk
from globus_sdk.scopes import AuthScopes
from globus_compute_sdk import Client
from globus_compute_sdk.sdk.login_manager import AuthorizerLoginManager
from globus_compute_sdk.sdk.login_manager.manager import ComputeScopeBuilder

import logging
log = logging.getLogger(__name__)

ComputeScopes = ComputeScopeBuilder()



# Exception to raise in case of errors
class ResourceServerError(Exception):
    pass


# Get App Client
def get_app_client():
    """Create a Globus confidential client using the Globus Application credentials."""
    return globus_sdk.ConfidentialAppAuthClient(
        #TODO: Make sure you select the endpoint credentials dynamically
        settings.POLARIS_ENDPOINT_ID, 
        settings.POLARIS_ENDPOINT_SECRET
    )


# Get tokens from Globus confidential client credentials
def get_tokens_from_globus_app():

    # Get access tokens using the service client credentials
    try:

        # Get Globus SDK from client with compute and openid scopes
        client = get_app_client()
        token_response = client.oauth2_client_credentials_tokens(
            requested_scopes=[
                AuthScopes.openid,
                ComputeScopes.all,
            ]
        )

        # Split tokens based on their scope
        openid_token = token_response.by_resource_server["auth.globus.org"]['access_token']
        compute_token = token_response.by_resource_server["funcx_service"]['access_token']

    # Error if getting tokens failed
    except globus_sdk.GlobusAPIError as e:
        raise ResourceServerError(f"Compute: Could not get access tokens from client application.")

    # Return tokens
    return openid_token, compute_token


# Get authenticated Compute Client using tokens
def get_compute_client_from_globus_app():
    """
    Create and return an authenticated Compute client using using existing tokens.

    Returns
    -------
        globus_compute_sdk.Client: Compute client to operate Globus Compute
    """

    try:
        # Get tokens from Globus Application
        openid_token, compute_token = get_tokens_from_globus_app()

        # Get Globus authorizers
        compute_auth = globus_sdk.AccessTokenAuthorizer(compute_token)
        openid_auth = globus_sdk.AccessTokenAuthorizer(openid_token)

        # Create Globus login manager using tokens
        compute_login_manager = AuthorizerLoginManager(
            authorizers={
                ComputeScopes.resource_server: compute_auth,
                AuthScopes.resource_server: openid_auth
            }
        )
        compute_login_manager.ensure_logged_in()

    except Exception as e:
        log.error("Exception in fetching client. Error",e)

    # Create Compute client
    return Client(login_manager=compute_login_manager)
