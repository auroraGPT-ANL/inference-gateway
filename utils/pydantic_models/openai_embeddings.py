from pydantic import BaseModel, Field
from typing import List, Optional, Union, Dict

# Extention of the Pydantic BaseModel that prevent extra attributes
class BaseModelExtraForbid(BaseModel):
    class Config:
        extra = 'forbid'

# OpenAI embeddings
# https://platform.openai.com/docs/api-reference/embeddings/create
class OpenAIEmbeddings(BaseModelExtraForbid):
    ...
