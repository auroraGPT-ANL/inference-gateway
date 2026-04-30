from datetime import datetime
from enum import Enum

from ninja import FilterSchema
from pydantic import BaseModel, ConfigDict, Field, computed_field


# Batch status
class BatchStatus(str, Enum):
    pending = "pending"
    running = "running"
    failed = "failed"
    completed = "completed"


class BatchLogSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    cluster: str
    framework: str
    input_file: str
    in_progress_at: datetime | None
    completed_at: datetime | None
    failed_at: datetime | None
    status: BatchStatus

    @computed_field
    @property
    def batch_id(self) -> str:
        return self.id


class BatchListFilter(FilterSchema):
    status: BatchStatus | None = None


class BatchSubmit(BaseModel):
    input_file: str = Field(..., min_length=1)
    model: str = Field(..., min_length=1)
    output_folder_path: str | None = None
