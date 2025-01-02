# Django imports
from django.conf import settings

# Globus imports
import globus_sdk
from globus_compute_sdk import Client, Executor

# Cache tools to limits how many calls are made to Globus servers
from cachetools import TTLCache, cached, LRUCache

import logging
log = logging.getLogger(__name__)


# Exception to raise in case of errors
class ResourceServerError(Exception):
    pass


# Get authenticated Compute Client using secret
#@cached(cache=LRUCache(maxsize=1024))
@cached(cache=TTLCache(maxsize=1024, ttl=60*60))
def get_compute_client_from_globus_app() -> globus_sdk.GlobusHTTPResponse:
    """
    Create and return an authenticated Compute client using the Globus SDK ClientApp.

    Returns
    -------
        globus_compute_sdk.Client: Compute client to operate Globus Compute
    """

    # Try to create and return the Compute client
    try:
        return Client(
            app=globus_sdk.ClientApp(
                client_id=settings.POLARIS_ENDPOINT_ID,
                client_secret=settings.POLARIS_ENDPOINT_SECRET
            )
        )
    except Exception as e:
        raise ResourceServerError("Exception in creating client. Error",e)


# Get authenticated Compute Executor using existing client
#@cached(cache=LRUCache(maxsize=1024))
@cached(cache=TTLCache(maxsize=1024, ttl=60*10))
def get_compute_executor(endpoint_id=None, client=None, amqp_port=443):
    """
    Create and return an authenticated Compute Executor using using existing client.

    Returns
    -------
        globus_compute_sdk.Executor: Compute Executor to operate Globus Compute
    """

    # Try to create and return the Compute executor
    try:
        return Executor(endpoint_id=endpoint_id, client=client, amqp_port=amqp_port)
    except Exception as e:
        raise ResourceServerError("Exception in creating executor. Error", e)


# Get endpoint status
@cached(cache=TTLCache(maxsize=1024, ttl=60))
def get_endpoint_status(endpoint_uuid=None, client=None, endpoint_slug=None):
    """
    Query the status of a Globus Compute endpoint. It caches the 
    result for x seconds to avoid generating a too-many-request
    from Globus services when sereval incoming requests target 
    an endpoint that is offline.
    """

    try:
        return client.get_endpoint_status(endpoint_uuid), ""
    except globus_sdk.GlobusAPIError as e:
        return None, f"Error: Cannot access the status of endpoint {endpoint_slug}: {e}"
    except Exception as e:
        return None, f"Error: Cannot access the status of endpoint {endpoint_slug}: {e}"
