from django.db import models

class Batch(models.Model):
    id = models.CharField(max_length=250, primary_key=True)
    
    # Who submitted the batch?
    name = models.CharField(max_length=100)
    username = models.CharField(max_length=100)

    # What did the user request?
    endpoint = models.CharField(max_length=250)
    input_file_path = models.CharField(max_length=250)
    machine = models.CharField(max_length=250)
    metadata = models.JSONField()

    # What is the status of the batch?
    errors = models.JSONField()
    status = models.CharField(max_length=250)
    output_file_path = models.CharField(max_length=250)
    error_file_path = models.CharField(max_length=250)
    created_at = models.DateTimeField()
    in_progress_at = models.DateTimeField()
    expires_at = models.DateTimeField()
    finalizing_at = models.DateTimeField()
    completed_at = models.DateTimeField()
    failed_at = models.DateTimeField()
    expired_at = models.DateTimeField()
    cancelling_at = models.DateTimeField()
    cancelled_at = models.DateTimeField()
    request_counts = models.JSONField()

    def __str__(self):
        return self.id