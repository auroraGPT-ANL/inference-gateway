"""
Metis cluster utility functions for direct API interactions.

Unlike Globus Compute endpoints, Metis models are already deployed behind
an API. This module provides utilities to:
- Fetch model status from Metis status endpoint
- Find matching models based on user requests
- Make direct API calls to Metis endpoints
"""

import json
import logging
import time
import httpx
import asyncio
from django.utils import timezone
from asgiref.sync import sync_to_async
from django.conf import settings
from typing import Dict, Tuple, Optional, List
from django.core.cache import cache
from resource_server_async.models import RequestLog

log = logging.getLogger(__name__)

# Cache configuration for Metis status
METIS_STATUS_CACHE_TTL = 60  # Cache status for 60 seconds
METIS_REQUEST_TIMEOUT = 120  # 2 minutes timeout for API requests


def get_metis_status_url() -> str:
    """Get Metis status URL from settings/env."""
    return getattr(settings, 'METIS_STATUS_URL', 'https://metis.alcf.anl.gov/status')


# Cache tokens for performance (avoid parsing JSON on every request)
_tokens_cache = None
_tokens_cache_time = 0
_tokens_cache_ttl = 60  # Cache for 60 seconds

def get_metis_api_tokens() -> Dict[str, str]:
    """
    Get Metis API tokens mapping from settings/env (cached for performance).
    
    Returns:
        Dictionary mapping endpoint UUIDs to API tokens
    """
    global _tokens_cache, _tokens_cache_time
    
    # Check cache
    current_time = time.time()
    if _tokens_cache is not None and (current_time - _tokens_cache_time) < _tokens_cache_ttl:
        return _tokens_cache
    
    # Parse tokens from settings
    import json
    tokens_json = getattr(settings, 'METIS_API_TOKENS', '{}')
    try:
        tokens = json.loads(tokens_json)
        if not isinstance(tokens, dict):
            log.error("METIS_API_TOKENS is not a valid JSON object")
            tokens = {}
        
        # Update cache
        _tokens_cache = tokens
        _tokens_cache_time = current_time
        return tokens
        
    except json.JSONDecodeError as e:
        log.error(f"Failed to parse METIS_API_TOKENS as JSON: {e}")
        return {}


def get_metis_api_token_for_endpoint(endpoint_id: str) -> str:
    """
    Get the API token for a specific Metis endpoint UUID (optimized with caching).
    
    Args:
        endpoint_id: The endpoint UUID from Metis status
    
    Returns:
        API token string, or empty string if not found
    """
    tokens = get_metis_api_tokens()
    token = tokens.get(endpoint_id, "")
    if not token:
        log.warning(f"No API token configured for Metis endpoint {endpoint_id}")
    return token


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
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(status_url)
            response.raise_for_status()
            status_data = response.json()
            
            # Cache the result
            if use_cache:
                cache.set(cache_key, status_data, METIS_STATUS_CACHE_TTL)
            
            log.info(f"Successfully fetched Metis status from {status_url}")
            return status_data, ""
            
    except httpx.TimeoutException:
        error_msg = f"Timeout fetching Metis status from {status_url}"
        log.error(error_msg)
        return None, error_msg
    except httpx.HTTPError as e:
        error_msg = f"HTTP error fetching Metis status: {e}"
        log.error(error_msg)
        return None, error_msg
    except json.JSONDecodeError as e:
        error_msg = f"Invalid JSON response from Metis status endpoint: {e}"
        log.error(error_msg)
        return None, error_msg
    except Exception as e:
        error_msg = f"Unexpected error fetching Metis status: {e}"
        log.error(error_msg)
        return None, error_msg


def find_metis_model(status_data: Dict, requested_model: str) -> Tuple[Optional[Dict], str, str]:
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
        
        # Check if requested model is in the experts list
        experts = model_info.get("experts", [])
        if requested_model in experts:
            endpoint_id = model_info.get("endpoint_id", "")
            log.info(f"Found matching Metis model: {model_key} for requested model {requested_model} (endpoint: {endpoint_id})")
            return model_info, endpoint_id, ""
    
    # Model not found or not live
    available_models = []
    for model_key, model_info in status_data.items():
        if model_info.get("status") == "Live":
            experts = model_info.get("experts", [])
            available_models.extend(experts)
    
    if available_models:
        error_msg = f"Error: Model '{requested_model}' not available on Metis. Available models: {', '.join(available_models)}"
    else:
        error_msg = "Error: No live models currently available on Metis"
    
    return None, "", error_msg


async def call_metis_api(
    model_info: Dict,
    endpoint_id: str,
    request_data: Dict,
    stream: bool = False
) -> Tuple[Optional[str], int, str]:
    """
    Make a direct API call to Metis endpoint.
    
    Args:
        model_info: Model information from Metis status
        endpoint_id: The endpoint UUID for API token lookup
        request_data: Request payload (OpenAI format)
        stream: Whether this is a streaming request
    
    Returns:
        Tuple of (response_text, status_code, error_message)
        - response_text: API response as string or None on error
        - status_code: HTTP status code
        - error_message: Error message if call failed, empty otherwise
    """
    api_url = model_info.get("url")
    if not api_url:
        return None, 500, "Error: Metis model info missing 'url' field"
    
    # Construct the full URL for chat completions
    full_url = f"{api_url}/chat/completions"
    
    # Get API token for this specific endpoint
    api_token = get_metis_api_token_for_endpoint(endpoint_id)
    if not api_token:
        return None, 401, f"Error: No API token configured for Metis endpoint {endpoint_id}"
    
    # Prepare headers
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_token}"
    }
    
    try:
        async with httpx.AsyncClient(timeout=METIS_REQUEST_TIMEOUT) as client:
            log.info(f"Making Metis API call to {full_url} (stream={stream})")
            
            # Non-streaming request
            response = await client.post(
                full_url,
                json=request_data,
                headers=headers
            )
            
            # Check for errors
            if response.status_code >= 400:
                error_body = response.text.strip()
                
                # Try to extract clean error message
                try:
                    error_json = json.loads(error_body)
                    # If Metis returns error in JSON format, extract the message
                    error_detail = error_json.get('error', {}).get('message', error_body) if isinstance(error_json.get('error'), dict) else error_json.get('message', error_body)
                except:
                    error_detail = error_body
                
                error_msg = f"Metis API error (HTTP {response.status_code}): {error_detail}"
                log.error(error_msg)
                return None, response.status_code, error_msg
            
            log.info(f"Metis API call successful (HTTP {response.status_code})")
            return response.text, response.status_code, ""
                
    except httpx.TimeoutException:
        error_msg = f"Timeout calling Metis API at {full_url} (timeout: {METIS_REQUEST_TIMEOUT}s)"
        log.error(error_msg)
        return None, 504, error_msg
    except httpx.HTTPError as e:
        error_msg = f"HTTP error calling Metis API: {e}"
        log.error(error_msg)
        return None, 500, error_msg
    except Exception as e:
        error_msg = f"Unexpected error calling Metis API: {e}"
        log.error(error_msg)
        return None, 500, error_msg


async def stream_metis_api(
    model_info: Dict,
    endpoint_id: str,
    request_data: Dict
):
    """
    Stream responses from Metis API (async generator).
    
    Args:
        model_info: Model information from Metis status
        endpoint_id: The endpoint UUID for API token lookup
        request_data: Request payload (OpenAI format)
    
    Yields:
        Chunks of streaming response from Metis API
        
    Raises:
        Exception: On API errors or connection issues
    """
    api_url = model_info.get("url")
    if not api_url:
        raise ValueError("Error: Metis model info missing 'url' field")
    
    # Construct the full URL for chat completions
    full_url = f"{api_url}/chat/completions"
    
    # Get API token for this specific endpoint
    api_token = get_metis_api_token_for_endpoint(endpoint_id)
    if not api_token:
        raise ValueError(f"Error: No API token configured for Metis endpoint {endpoint_id}")
    
    # Prepare headers
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_token}"
    }
    
    # Ensure stream is set to True in request data
    request_data["stream"] = True
    
    log.info(f"Starting Metis streaming API call to {full_url}")
    
    try:
        async with httpx.AsyncClient(timeout=METIS_REQUEST_TIMEOUT) as client:
            async with client.stream("POST", full_url, json=request_data, headers=headers) as response:
                # Check for errors before streaming
                if response.status_code >= 400:
                    error_text = await response.aread()
                    error_body = error_text.decode().strip()
                    
                    # Try to extract clean error message
                    try:
                        error_json = json.loads(error_body)
                        # If Metis returns error in JSON format, extract the message
                        error_detail = error_json.get('error', {}).get('message', error_body) if isinstance(error_json.get('error'), dict) else error_json.get('message', error_body)
                    except:
                        error_detail = error_body
                    
                    # Create clean error message
                    error_msg = f"HTTP {response.status_code}: {error_detail}"
                    log.error(f"Metis API error - {error_msg}")
                    
                    # Raise with just the clean message (not nested)
                    raise ValueError(error_msg)
                
                # Stream the response
                async for chunk in response.aiter_text():
                    if chunk:
                        yield chunk
                        
        log.info(f"Metis streaming API call completed successfully")
                        
    except httpx.TimeoutException as e:
        log.error(f"Timeout during Metis streaming: {e}")
        raise ValueError(f"Request timeout after {METIS_REQUEST_TIMEOUT}s")
    except ValueError:
        # Re-raise ValueError with clean message (from our error handling above)
        raise
    except httpx.HTTPError as e:
        log.error(f"HTTP error during Metis streaming: {e}")
        raise ValueError(f"Network error: {str(e)}")
    except Exception as e:
        log.error(f"Unexpected error during Metis streaming: {e}")
        raise ValueError(f"Unexpected error: {str(e)}")


def format_metis_status_for_list_endpoints(status_data: Dict) -> List[Dict]:
    """
    Format Metis status data for list-endpoints response.
    
    Args:
        status_data: Raw Metis status dictionary
    
    Returns:
        List of model dictionaries with name and status
    """
    models = []
    
    for model_key, model_info in status_data.items():
        if model_info.get("status") == "Live":
            experts = model_info.get("experts", [])
            # Add each expert as a separate model entry
            for expert in experts:
                if isinstance(expert, str) and len(expert) > 0:
                    # Do not expose sensitive metis_key to users
                    models.append({
                        "name": expert,
                        "endpoint_status": "online",
                        "model_status": "running",
                        "description": model_info.get("description", "")
                    })
    
    return models


async def update_metis_streaming_log(log_id, streaming_state: dict, requested_model: str):
    """
    Background task to update RequestLog after Metis streaming completes.
    
    Optimized to:
    - Wait efficiently for completion
    - Update database once
    - Handle errors gracefully
    """
    try:
        # Wait for completion (efficient polling with timeout)
        max_wait = 600  # 10 minutes
        waited = 0
        poll_interval = 0.5  # 500ms
        
        while not streaming_state['completed'] and waited < max_wait:
            await asyncio.sleep(poll_interval)
            waited += poll_interval
        
        # Get metrics
        duration = time.time() - streaming_state['start_time']
        total_chunks = streaming_state['total_chunks']
        
        # Update database (single query)
        db_log = await sync_to_async(RequestLog.objects.get)(id=log_id)
        
        if streaming_state['error']:
            db_log.result = f"error: {streaming_state['error']}"
            log.error(f"Metis streaming failed for {requested_model}: {streaming_state['error']}")
        else:
            # Store limited chunks or completion marker
            db_log.result = "\n".join(streaming_state['chunks']) if streaming_state['chunks'] else "streaming_completed"
            log.info(f"Metis streaming completed for {requested_model}: {total_chunks} chunks in {duration:.2f}s")
        
        db_log.timestamp_compute_response = timezone.now()
        await sync_to_async(db_log.save, thread_sensitive=True)()
        
    except Exception as e:
        log.error(f"Error in update_metis_streaming_log: {e}")