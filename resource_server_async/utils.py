import ast
import importlib
import json
import logging
from typing import Any

from django.forms.models import model_to_dict
from django.http import HttpRequest
from django.utils import timezone
from django.utils.text import slugify

from resource_server_async.clusters.cluster import BaseCluster
from resource_server_async.endpoints.endpoint import BaseEndpoint
from resource_server_async.models import (
    BatchLog,
    Cluster,
    Endpoint,
)
from resource_server_async.schemas.batch import BatchStatus, BatchSubmit
from utils.pydantic_models.openai_chat_completions import OpenAIChatCompletionsPydantic
from utils.pydantic_models.openai_completions import OpenAICompletionsPydantic
from utils.pydantic_models.openai_embeddings import OpenAIEmbeddingsPydantic

from .errors import ClusterNotFound, EndpointNotFound

log = logging.getLogger(__name__)  # Add logger


# Exception to raise in case of errors
class ResourceServerError(Exception):
    pass


# Extract user prompt
def extract_prompt(model_params):
    """Extract the user input text from the requested model parameters."""

    # Completions
    if "prompt" in model_params:
        return model_params["prompt"]

    # Chat completions
    elif "messages" in model_params:
        return model_params["messages"]

    # Embeddings
    elif "input" in model_params:
        return model_params["input"]

    # Undefined
    return "default"


# Validate request body
# TODO: Use validate_body to reduce code duplication
def validate_request_body(request, openai_endpoint):
    """Build data dictionary for inference request if user inputs are valid."""

    # Strip the last forward slash if needed (to be consistent with cluster's openai_endpoints)
    if openai_endpoint[-1] == "/":
        openai_endpoint = openai_endpoint[:-1]

    # Select the appropriate pydantic model for data validation
    if "chat/completions" in openai_endpoint:
        pydantic_class = OpenAIChatCompletionsPydantic
    elif "completion" in openai_endpoint:
        pydantic_class = OpenAICompletionsPydantic
    elif "embeddings" in openai_endpoint:
        pydantic_class = OpenAIEmbeddingsPydantic
    else:
        return {"error": f"Error: {openai_endpoint} endpoint not supported."}

    # Decode and validate request body
    model_params = validate_body(request, pydantic_class)
    if "error" in model_params.keys():
        return model_params

    # Add the 'url' parameter to model_params
    model_params["openai_endpoint"] = openai_endpoint

    # Build request data if nothing wrong was caught
    return {"model_params": model_params}


# Validate batch body
def validate_batch_body(request):
    """Build data dictionary for inference batch request if user inputs are valid."""
    return validate_body(request, BatchSubmit)


# Helper function to safely decode request body
def decode_request_body(request: HttpRequest) -> str:
    """
    Safely decode request.body to string, handling both bytes and str.

    Django Ninja can return either bytes or str depending on context.

    Args:
        request: Django request object

    Returns:
        str: Decoded body as string
    """
    body = request.body
    if isinstance(body, bytes):
        return body.decode("utf-8")
    return body


# Update batch entry
async def update_batch(batch: BatchLog) -> BatchLog:
    """Update batch database entry and return the modified BatchLog instance data."""

    if not batch.task_ids:
        log.warning("Cannot update batch with missing task_ids")
        return batch

    endpoint = await load_endpoint_adapter(batch.cluster, batch.framework, batch.model)

    # Get latest batch status
    response = await endpoint.get_batch_status(batch)
    status = response.status
    result = response.result

    # No status change:
    if batch.status == status:
        return batch

    # Update status and result
    batch.status = status

    # Adjust timestamp
    if batch.status == BatchStatus.failed:
        batch.failed_at = timezone.now()
    elif batch.status == BatchStatus.completed:
        batch.completed_at = timezone.now()

    # Try to parse metrics summary from result if available
    if result:
        batch.result = result

        total_tokens = None
        num_responses = None
        response_time_sec = None
        throughput = None

        try:
            result_data: dict[str, Any] = json.loads(batch.result)
            if "metrics" in result_data:
                metrics: dict[str, Any] = result_data.get("metrics", {})
                total_tokens = metrics.get("total_tokens")
                num_responses = metrics.get("num_responses")
                response_time_sec = metrics.get("response_time_sec")
                throughput = metrics.get("throughput_tokens_per_sec")
        except Exception:
            pass
        else:
            await batch.create_or_update_metrics(
                total_tokens=total_tokens,
                num_responses=num_responses,
                response_time_sec=response_time_sec,
                throughput_tokens_per_sec=throughput,
            )

    await batch.asave()
    return batch


async def load_endpoint_adapter(
    cluster: str, framework: str, model: str
) -> BaseEndpoint:
    """Extract the endpoint from the database and return its underlying adapter object."""

    endpoint_slug = slugify(f"{cluster} {framework} {model.lower()}")
    try:
        db_endpoint = await Endpoint.objects.aget(endpoint_slug=endpoint_slug)
    except Endpoint.DoesNotExist:
        raise EndpointNotFound(
            f"The requested endpoint {endpoint_slug!r} does not exist."
        )

    # Convert the config field into a dictionary
    endpoint_dictionary = model_to_dict(db_endpoint)
    endpoint_dictionary["config"] = ast.literal_eval(db_endpoint.config)

    # Extract the adapter class from the endpoint's database configuration
    parts = db_endpoint.endpoint_adapter.rsplit(".", 1)
    module = importlib.import_module(parts[0])
    AdapterClass = getattr(module, parts[1])

    # Make sure the adaptor inherits from the BaseEndpoint generic class
    if not issubclass(AdapterClass, BaseEndpoint):
        raise AssertionError(
            f"Endpoint adapter {db_endpoint.endpoint_adapter} should inherit from BaseEndpoint."
        )

    # Instantiate the adaptor class
    endpoint = AdapterClass(**endpoint_dictionary)
    return endpoint


async def load_cluster_adapter(cluster_name: str) -> BaseCluster:
    """Extract the cluster from the database and return its underlying wrapper object."""
    try:
        db_cluster = await Cluster.objects.aget(cluster_name=cluster_name)
    except Cluster.DoesNotExist:
        raise ClusterNotFound(f"The requested cluster {cluster_name!r} does not exist.")

    # Convert the config field into a dictionary
    cluster_dictionary = model_to_dict(db_cluster)
    cluster_dictionary["config"] = ast.literal_eval(db_cluster.config)

    # Extract the adapter class from the cluster's database configuration
    parts = db_cluster.cluster_adapter.rsplit(".", 1)
    module = importlib.import_module(parts[0])
    AdapterClass = getattr(module, parts[1])

    # Make sure the adaptor inherits from the BaseCluster generic class
    if not issubclass(AdapterClass, BaseCluster):
        raise AssertionError(
            f"Cluster adapter {db_cluster.cluster_adapter} should inherit from BaseCluster."
        )

    # Instantiate the adaptor class
    cluster_adapter = AdapterClass(**cluster_dictionary)
    return cluster_adapter
