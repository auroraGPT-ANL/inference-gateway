from pydantic import BaseModel, Field
from ninja import FilterSchema
from enum import Enum
from typing import Optional


# Batch status
class BatchStatusEnum(str, Enum):
    pending = "pending"
    running = "running"
    failed = "failed"
    completed = "completed"


class BatchListFilter(FilterSchema):
    status: BatchStatusEnum = None


# Extention of the Pydantic BaseModel that prevent extra attributes
class BaseModelExtraForbid(BaseModel):
    class Config:
        extra = "forbid"


# Batch request
class BatchPydantic(BaseModelExtraForbid):
    input_file: str = Field(..., min_length=1)
    model: str = Field(..., min_length=1)
    output_folder_path: Optional[str] = Field(default=None)
