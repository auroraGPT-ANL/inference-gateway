from pydantic import BaseModel, Field
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