from pydantic import BaseModel
from django.core.exceptions import ValidationError
from django.conf import settings
from django.db import models
from django.utils.timezone import now
import uuid


# Supported authentication origins
class AuthService(models.TextChoices):
    GLOBUS = "globus", "Globus"


# User model
class UserPydantic(BaseModel):
    id: str
    name: str
    username: str
    email: str
    idp_id: str
    idp_name: str
    auth_service: str
class User(models.Model):
    """Details about a user who was authorized to access the service"""

    # User info
    id = models.CharField(max_length=100, primary_key=True)
    name = models.CharField(max_length=100)
    username = models.CharField(max_length=100)
    email = models.CharField(max_length=100)

    # Identity provider info
    idp_id = models.CharField(max_length=100, default="", blank=True)
    idp_name = models.CharField(max_length=100)

    # Where the user info is coming from (e.g. Globus, Slack)
    auth_service = models.CharField(max_length=100, choices=AuthService.choices, default=AuthService.GLOBUS.value)

    # Custom display
    def __str__(self):
        return f"<User - {self.name} - {self.username} - {self.idp_name}>"
    
    # Overwrite save to database function
    def save(self, *args, **kwargs):

        # Raise an error if the user should not be authorized to use the service
        # With Globus, username must be used (email only shows the primary username of the linked identities)
        if not self.username.split("@")[-1] in settings.AUTHORIZED_IDP_DOMAINS:
            raise ValidationError(f"IdP domain {self.username} not authorized for User model.")
        if not self.idp_id in settings.AUTHORIZED_IDP_UUIDS:
            raise ValidationError(f"IdP UUID {self.idp_id} not authorized for User model.")

        # Save model if authorized
        super().save(*args, **kwargs)


# Access log model
class AccessLogPydantic(BaseModel):
    id: str
    user: any
    timestamp_request: str
    timestamp_response: str
    api_route: str
    origin_ip: str
    status_code: str
    error: str
class AccessLog(models.Model):

    # Unique access ID
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # User details (link to the User model)
    user = models.ForeignKey(
        User, 
        on_delete=models.PROTECT, 
        related_name='access_logs', # reverse relation (e.g. user.access_logs.all())
        db_index=True
    )

    # Timestamps (request received and response returned)
    timestamp_request = models.DateTimeField(null=True)
    timestamp_response = models.DateTimeField(null=True)

    # Which API route was requested (e.g. /resource_server/list-endpoints)
    api_route = models.CharField(max_length=256, null=False, blank=False)

    # IP address from where the request is coming from
    origin_ip = models.CharField(max_length=50, null=False, blank=False)    

    # HTTP status of the request
    status_code = models.IntegerField(null=False)

    # Error message if any
    error = models.TextField(null=True)

    # Custom display
    def __str__(self):
        return f"<Access - {self.user.username} - {self.api_route} - {self.status_code}>"


# Request log model
class RequestLogPydantic(BaseModel):
    id: str
    access_log: str
    cluster: str
    framework: str
    model: str
    openai_endpoint: str
    timestamp_compute_request: str
    timestamp_compute_response: str
    prompt: str
    result: str
    task_uuid: str
class RequestLog(models.Model):

    # Unique request ID
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Link to the access log tied to this request
    access_log = models.OneToOneField(
        AccessLog,
        on_delete=models.PROTECT,
        related_name='request_log', # reverse relation (e.g., access_log.request_log)
        db_index=True,
    )

    # Inference endpoint details
    cluster = models.CharField(max_length=100, null=False, blank=False)
    framework = models.CharField(max_length=100, null=False, blank=False)
    model = models.CharField(max_length=100, null=False, blank=False)
    openai_endpoint = models.CharField(max_length=100, null=False, blank=False)
    
    # Timestamps (before and after the remote computation)
    timestamp_compute_request = models.DateTimeField(null=False, blank=False)
    timestamp_compute_response = models.DateTimeField(null=False, blank=False)

    # Inference data
    prompt = models.TextField(null=True)
    result = models.TextField(null=True)

    # Compute task ID
    task_uuid = models.CharField(null=True, max_length=100)

    # Custom display
    def __str__(self):
        return f"<Request - {self.access_log.user.username} - {self.cluster} - {self.framework} - {self.model}>"
