from utils.serializers import (
    OpenAICompletionsParamSerializer, 
    OpenAIChatCompletionsParamSerializer, 
    OpenAIEmbeddingsParamSerializer
)
from rest_framework.exceptions import ValidationError
import json
from uuid import UUID

# Constants
ALLOWED_FRAMEWORKS = {
    "polaris": ["llama-cpp", "vllm"],
    "sophia": ["vllm"]
}
ALLOWED_OPENAI_ENDPOINTS = {
    "polaris": ["chat/completions", "completions", "embeddings"],
    "sophia": ["chat/completions", "completions", "embeddings"]
}
ALLOWED_CLUSTERS = list(ALLOWED_FRAMEWORKS.keys())

# Exception to raise in case of errors
class ResourceServerError(Exception):
    pass


# Validate URL inputs
def validate_url_inputs(cluster: str, framework: str, openai_endpoint: str):
    """Validate user inputs from POST requests."""

    # Error message if cluster not available
    if not cluster in ALLOWED_CLUSTERS:
        return f"Error: {cluster} cluster not supported. Currently supporting {ALLOWED_CLUSTERS}."
    
    # Error message if framework not available
    if not framework in ALLOWED_FRAMEWORKS[cluster]:
        return f"Error: {framework} framework not supported. Currently supporting {ALLOWED_FRAMEWORKS[cluster]}."
    
    # Error message if openai endpoint not available
    if not openai_endpoint in ALLOWED_OPENAI_ENDPOINTS[cluster]:
        return f"Error: {openai_endpoint} openai endpoint not supported. Currently supporting {ALLOWED_OPENAI_ENDPOINTS[cluster]}."

    # No error message if the inputs are valid
    return ""


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
def validate_request_body(request, openai_endpoint):
    """Build data dictionary for inference request if user inputs are valid."""
        
    # Select the appropriate data validation serializer based on the openai endpoint
    if "chat/completions" in openai_endpoint:
        serializer_class = OpenAIChatCompletionsParamSerializer
    elif "completion" in openai_endpoint:
        serializer_class = OpenAICompletionsParamSerializer
    elif "embeddings" in openai_endpoint:
        serializer_class = OpenAIEmbeddingsParamSerializer
    else:
        return {"error": f"Error: {openai_endpoint} endpoint not supported."}
        
    # Decode request body into a dictionary
    try:
        model_params = json.loads(request.body.decode("utf-8"))
    except:
        return {"error": f"Error: Request body cannot be decoded."}
        
    # Send an error if the input data is not valid
    try:
        serializer = serializer_class(data=model_params)
        is_valid = serializer.is_valid(raise_exception=True)
    except ValidationError as e:
        return {"error": f"Error: Could not validate data: {e}"}

    # Add the 'url' parameter to model_params
    model_params['openai_endpoint'] = openai_endpoint

    # Build request data if nothing wrong was caught
    return {"model_params": model_params}


# Extract group UUIDs from an allowed_globus_groups model field
def extract_group_uuids(globus_groups):
    """Extract group UUIDs from an allowed_globus_groups model field."""

    # Make sure the globus_groups argument is a string
    if not isinstance(globus_groups, str):
        return [], "Error: globus_groups must be a string like 'group1-name:group1-uuid; group2-name:group2-uuid; ...' "

    # Return empty list if no group restriction was provided
    if len(globus_groups) == 0:
        return []

    # Declare the list of group UUIDs
    group_uuids = []

    # Append each UUID to the list
    try:
        for group_name_uuid in globus_groups.split(";"):
            group_uuids.append(group_name_uuid.split(":")[-1])
    except Exception as e:
        return [], f"Error: Exception while extracting Globus Group UUIDs. {e}"
    
    # Make sure that all UUID strings have the UUID format
    for uuid_to_test in group_uuids:
        try:
            uuid_obj = UUID(uuid_to_test).version
        except Exception as e:
            return [], f"Error: Could not extract UUID format from the database. {e}"
    
    # Return the list of group UUIDs
    return group_uuids, ""