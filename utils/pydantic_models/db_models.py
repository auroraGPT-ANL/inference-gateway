from datetime import datetime
from pydantic import BaseModel, Field
from typing import Any, Optional

class UserPydantic(BaseModel):
    id: str
    name: str
    username: str
    email: str
    idp_id: str
    idp_name: str
    auth_service: str

class AccessLogPydantic(BaseModel):
    id: str
    user: Any
    timestamp_request: datetime
    timestamp_response: Optional[datetime] = Field(default=None)
    api_route: str
    origin_ip: Optional[str] = Field(default=None)
    status_code: Optional[str] = Field(default=None)
    error: Optional[str] = Field(default=None)

class RequestLogPydantic(BaseModel):
    id: str
    access_log: Optional[str] = Field(default=None)
    cluster: Optional[str] = Field(default=None)
    framework: Optional[str] = Field(default=None)
    model: Optional[str] = Field(default=None)
    openai_endpoint: Optional[str] = Field(default=None)
    timestamp_compute_request: Optional[datetime] = Field(default=None)
    timestamp_compute_response: Optional[datetime] = Field(default=None)
    prompt: Optional[str] = Field(default=None)
    result: Optional[str] = Field(default=None)
    task_uuid: Optional[str] = Field(default=None)