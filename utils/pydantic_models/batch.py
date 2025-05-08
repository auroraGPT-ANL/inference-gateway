from utils.pydantic_models.openai_chat_completions import OpenAIChatCompletionsPydantic
from utils.pydantic_models.openai_completions import OpenAICompletionsPydantic
from utils.pydantic_models.openai_embeddings import OpenAIEmbeddingsPydantic
from pydantic import BaseModel, Field, model_validator
from enum import Enum
from typing import Optional

# Extention of the Pydantic BaseModel that prevent extra attributes
class BaseModelExtraForbid(BaseModel):
    class Config:
        extra = 'forbid'

# Batch request
class BatchPydantic(BaseModelExtraForbid):
    input_file: str = Field(..., min_length=1)
    model: str = Field(..., min_length=1)
    output_folder_path: Optional[str] = Field(default=None)

# Batch method
class BatchMethod(str, Enum):
    POST = "POST"

# Batch URL
class BatchURL(str, Enum):
    v1_completions = "/v1/completions"
    v1_embeddings = "/v1/embeddings"
    v1_chat_completions = "/v1/chat/completions"

# Uploaded batch file
class UploadedBatchFilePydantic(BaseModelExtraForbid):
    custom_id: str = Field(..., min_length=1)
    method: BatchMethod
    url: BatchURL
    body: dict

    # Validation depending on url value
    @model_validator(mode="before")
    def validate_body(cls, values):

        # Extract url and body
        input_url = values.get("url")
        input_body = values.get("body")

        # Error if the URL is not available
        valid_urls = [url.value for url in BatchURL]
        if not input_url in valid_urls:
             raise ValueError(f"'url' must be one of {valid_urls}. Provided was: '{input_url}'.")

        # Define the validation class options
        pydantic_class = {
            BatchURL.v1_embeddings.value: OpenAIEmbeddingsPydantic,
            BatchURL.v1_completions.value: OpenAICompletionsPydantic,
            BatchURL.v1_chat_completions.value: OpenAIChatCompletionsPydantic
        }
        
        # Validate inputs
        _ = pydantic_class[input_url](**input_body)
        
        # Return values if nothing wrong happened in the valudation step
        return values


# Batch status
class BatchStatusEnum(str, Enum):
    pending = 'pending'
    running = 'running'
    failed = 'failed'
    completed = 'completed'