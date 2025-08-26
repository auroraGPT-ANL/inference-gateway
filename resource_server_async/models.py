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


# Access log model
class AccessLog(models.Model):

    # Unique access ID
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # User details (link to the User model)
    user = models.ForeignKey(
        User, 
        on_delete=models.PROTECT, 
        related_name='access_logs', # reverse relation (e.g. user.access_logs.all())
        db_index=True,
        null=True # For when an AccessLog is used to report un-authorized attempts
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
        if self.user:
            username = self.user.username
        else:
            username = "Unauthorized"
        return f"<Access - {username} - {self.api_route} - {self.status_code}>"


# Request log model
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


# Batch log model
class BatchLog(models.Model):

    # Unique request ID
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Link to the access log tied to this request
    access_log = models.OneToOneField(
        AccessLog,
        on_delete=models.PROTECT,
        related_name='batch_log', # reverse relation (e.g., access_log.batch_log)
        db_index=True,
    )
    
    # What did the user request?
    input_file = models.CharField(max_length=500)
    output_folder_path = models.CharField(max_length=500, blank=True)
    cluster = models.CharField(max_length=100)
    framework = models.CharField(max_length=100)
    model = models.CharField(max_length=250)

    # List of Globus task UUIDs tied to the batch (string separated with ,)
    globus_batch_uuid = models.CharField(max_length=100)
    globus_task_uuids = models.TextField(null=True)
    result = models.TextField(blank=True)

    # What is the status of the batch?
    status = models.CharField(max_length=250, default="pending")
    in_progress_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    failed_at = models.DateTimeField(null=True, blank=True)