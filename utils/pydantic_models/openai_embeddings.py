from pydantic import BaseModel, Field, model_validator
from typing import List, Optional, Union
from enum import Enum

# Extention of the Pydantic BaseModel that prevent extra attributes
class BaseModelExtraForbid(BaseModel):
    class Config:
        extra = 'forbid'

# Encoding format
class EncodingFormat(str, Enum):
    float = "float"
    base64 = "base64"

# OpenAI embeddings
# https://platform.openai.com/docs/api-reference/embeddings/create
class OpenAIEmbeddings(BaseModelExtraForbid):
    input: Union[str, List[str], List[int], List[List[int]]]
    model: str
    dimensions: Optional[int] = Field(default=None, ge=1)
    encoding_format: Optional[EncodingFormat] = Field(default=EncodingFormat.float.value)
    user: Optional[str] = Field(default=None)

    # Extra validations
    @model_validator(mode='after')
    def extra_validations(cls, values):

        # Check length of input arrays
        min_length = 1
        max_lentgh = 2048
        error_message = "Length of all 'input' arrays must be between 1 and 2048."
        if isinstance(values.input, list):
            if len(values.input) > max_lentgh or len(values.input) < min_length:
                raise ValueError(error_message)
            for item in values.input:
                if isinstance(item, list):
                    if len(item) > max_lentgh or len(item) < min_length:
                        raise ValueError(error_message)

        # Return values if nothing wrong happened in the valudation step
        return values