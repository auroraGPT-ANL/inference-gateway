from django.db import models
import uuid
from django.utils.timezone import now


class Batch(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    # Who submitted the batch?
    name = models.CharField(max_length=100)
    username = models.CharField(max_length=100)

    # What did the user request?
    endpoint = models.CharField(max_length=250)
    input_file_id = models.CharField(max_length=250) # actually a file path
    machine = models.CharField(max_length=250)
    metadata = models.JSONField(default=dict)

    # What is the status of the batch?
    errors = models.JSONField(default=dict)
    status = models.CharField(max_length=250, default='pending')
    output_file_id = models.CharField(max_length=250, blank=True)
    error_file_id = models.CharField(max_length=250, blank=True)
    created_at = models.DateTimeField(default=now)
    in_progress_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    finalizing_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    failed_at = models.DateTimeField(null=True, blank=True)
    expired_at = models.DateTimeField(null=True, blank=True)
    cancelling_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    request_counts = models.JSONField(default=dict)

    def __str__(self):
        return self.id