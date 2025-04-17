from pydantic import BaseModel, Field, model_validator
from typing import List, Optional, Union, Dict

# Extention of the Pydantic BaseModel that prevent extra attributes
class BaseModelExtraForbid(BaseModel):
    class Config:
        extra = 'forbid'

# OpenAI stream_options
class OpenAIStreamOptions(BaseModelExtraForbid):
    include_usage: bool

# OpenAI completions
# https://platform.openai.com/docs/api-reference/completions/create
class OpenAICompletionsPydantic(BaseModelExtraForbid):
    prompt: Union[str, List[str], List[int], List[List[int]]]
    model: str = Field(..., min_length=1)
    best_of: Optional[int] = Field(default=1, ge=0, le=20)
    echo: Optional[bool] = Field(default=False)
    frequency_penalty: Optional[float] = Field(default=0, ge=-2, le=2)
    logit_bias: Optional[Dict[str, float]] = Field(default=None)
    logprobs: Optional[int] = Field(default=None, ge=0, le=5)
    max_tokens: Optional[int] = Field(default=16, ge=0)
    n: Optional[int] = Field(default=1, ge=1, le=128)
    presence_penalty: Optional[float] = Field(default=0, ge=-2, le=2)
    seed: Optional[int] = Field(default=None, ge=-9223372036854775808, le=9223372036854775807)
    stop: Optional[Union[str, List[str]]] = Field(default=None, max_items=4, min_items=1)
    stream: Optional[bool] = Field(default=False)
    stream_options: Optional[OpenAIStreamOptions] = Field(default=None)
    suffix: Optional[str] = Field(default=None)
    temperature: Optional[float] = Field(default=1, ge=0, le=2)
    top_p: Optional[float] = Field(default=1, ge=0, le=1)
    user: Optional[str] = Field(default=None)

    # Extra validations
    @model_validator(mode='after')
    def extra_validations(cls, values):
            
        # Validate logit_bias bias values
        if isinstance(values.logit_bias, dict):
            for bias in values.logit_bias.values():
                if bias < -100 or bias > 100:
                    raise ValueError("'logit_bias' bias values must be from -100 to 100.")

        # Return values if nothing wrong happened in the valudation step
        return values