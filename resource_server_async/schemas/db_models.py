from datetime import datetime
from typing import Any, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class UserPydantic(BaseModel):
    id: str
    name: str
    username: str
    user_group_uuids: List[str]
    idp_id: str
    idp_name: str
    auth_service: str


class AccessLogPydantic(BaseModel):
    id: str
    user: Any
    timestamp_request: datetime
    timestamp_response: Optional[datetime] = None
    api_route: str
    origin_ip: Optional[str] = None
    status_code: Optional[int] = None
    error: Optional[str] = None
    authorized_groups: Optional[str] = None


class RequestLogPydantic(BaseModel):
    id: str
    access_log: Optional[Any] = None  # AccessLog object
    cluster: Optional[str] = None
    framework: Optional[str] = None
    model: Optional[str] = None
    openai_endpoint: Optional[str] = None
    timestamp_compute_request: Optional[datetime] = None
    timestamp_compute_response: Optional[datetime] = None
    prompt: Optional[str] = None
    result: Optional[str] = None
    task_uuid: Optional[str] = None


class BatchLogPydantic(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    access_log: Optional[Any] = None  # AccessLog object
    input_file: Optional[str] = None
    output_folder_path: Optional[str] = None
    cluster: Optional[str] = None
    framework: Optional[str] = None
    model: Optional[str] = None
    globus_batch_uuid: Optional[str] = None
    task_ids: Optional[str] = None
    result: Optional[str] = Field(default="")
    status: Optional[str] = None
    in_progress_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    failed_at: Optional[datetime] = None
