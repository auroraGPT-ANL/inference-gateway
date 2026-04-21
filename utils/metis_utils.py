"""
Metis cluster utility functions for direct API interactions.

Unlike Globus Compute endpoints, Metis models are already deployed behind
an API. This module provides utilities to:
- Fetch model status from Metis status endpoint
- Find matching models based on user requests
- Make direct API calls to Metis endpoints
"""

import logging
from typing import Dict, Optional, Tuple

from django.conf import settings
from django.core.cache import cache

from resource_server_async.utils import (
    SubmitHTTPXCallResponse,
    httpx_call_methods,
    submit_httpx_call,
)

log = logging.getLogger(__name__)

# Cache configuration for Metis status
METIS_STATUS_CACHE_TTL = 60  # Cache status for 60 seconds
METIS_REQUEST_TIMEOUT = 120  # 2 minutes timeout for API requests


def get_metis_status_url() -> str:
    """Get Metis status URL from settings/env."""
    return getattr(settings, "METIS_STATUS_URL", "https://metis.alcf.anl.gov/status")


# Cache tokens for performance (avoid parsing JSON on every request)
_tokens_cache = None
_tokens_cache_time = 0
_tokens_cache_ttl = 60  # Cache for 60 seconds


async def fetch_metis_status(use_cache: bool = True) -> Tuple[Optional[Dict], str]:
    """
    Fetch status information from Metis status endpoint.

    Args:
        use_cache: Whether to use cached status (default: True)

    Returns:
        Tuple of (status_dict, error_message)
        - status_dict: Dictionary with model information or None on error
        - error_message: Error message if fetch failed, empty string otherwise
    """
    cache_key = "metis_status_data"

    # Try cache first if enabled
    if use_cache:
        cached_status = cache.get(cache_key)
        if cached_status is not None:
            log.debug("Using cached Metis status")
            return cached_status, ""

    # Fetch from API
    status_url = get_metis_status_url()
    httpx_response: SubmitHTTPXCallResponse = await submit_httpx_call(
        status_url,
        timeout=30,
        method=httpx_call_methods.get,
    )

    # Send error if any
    if httpx_response.error_message:
        return None, httpx_response.error_message

    # Extract data
    status_data = httpx_response.result

    # Cache and return result
    if use_cache:
        cache.set(cache_key, status_data, METIS_STATUS_CACHE_TTL)
    return status_data, ""


def find_metis_model(
    status_data: Dict, requested_model: str
) -> Tuple[Optional[Dict], str, str]:
    """
    Find a matching Metis model based on requested model name.

    Args:
        status_data: Metis status dictionary
        requested_model: Model name requested by user

    Returns:
        Tuple of (model_info, endpoint_id, error_message)
        - model_info: Dictionary with model details or None if not found
        - endpoint_id: The endpoint UUID for API token lookup
        - error_message: Error message if model not found/unavailable, empty otherwise
    """
    if not status_data:
        return None, "", "Error: Metis status data is empty"

    # Search through all models
    for model_key, model_info in status_data.items():
        # Check if status is Live
        if model_info.get("status") != "Live":
            continue

        # Check if this is the requested model
        # experts = model_info.get("experts", [])
        # if requested_model in experts:
        if requested_model == model_info.get("model", ""):
            endpoint_id = model_info.get("endpoint_id", "")
            log.info(
                f"Found matching Metis model: {model_key} for requested model {requested_model} (endpoint: {endpoint_id})"
            )
            return model_info, endpoint_id, ""

    # Model not found or not live
    available_models = []
    for model_key, model_info in status_data.items():
        if model_info.get("status") == "Live":
            # experts = model_info.get("experts", [])
            # available_models.extend(experts)
            models = model_info.get("model", [])
            available_models.extend(models)

    if available_models:
        error_msg = f"Error: Model '{requested_model}' not available on Metis. Available models: {', '.join(available_models)}"
    else:
        error_msg = "Error: No live models currently available on Metis"

    return None, "", error_msg
