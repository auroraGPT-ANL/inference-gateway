from django.db import models
from django.utils.text import slugify
import uuid
from django.utils.timezone import now
from django.core.exceptions import ValidationError

# Details of a given Globus Compute endpoint
class Endpoint(models.Model):

    # Slug for the endpoint
    # In the form of <cluster>-<framework>-<model> (all lower case)
    # An example is polaris-llama-cpp_meta-llama3-8b-instruct
    endpoint_slug = models.SlugField(
        max_length=100,
        unique=True,
    )

    # HPC machine the endpoint is running on (e.g. polaris)
    cluster = models.CharField(max_length=100)

    # Framework (e.g. vllm, llama_cpp, deepspeed)
    framework = models.CharField(max_length=100)

    # Model name (e.g. cpp_meta-Llama3-8b-instruct)
    model = models.CharField(max_length=100)

    # Globus Compute single-request UUIDs
    endpoint_uuid = models.CharField(max_length=100)
    function_uuid = models.CharField(max_length=100)

    # Globus Compute batch-request UUIDs
    batch_endpoint_uuid = models.CharField(max_length=100, default="", blank=True)
    batch_function_uuid = models.CharField(max_length=100, default="", blank=True)

    # API port (for distinct models running on the same node)
    api_port = models.IntegerField(default=8000)

    # Groups that are allowed to access the endpoint (no restriction if empty)
    # Format: "group1-name:group1-uuid; group2-name:group2-uuid; ..."
    allowed_globus_groups = models.TextField(default="", blank=True)

    # String function
    def __str__(self):
        return f"<Endpoint {self.endpoint_slug}>"

    # Automatically generate slug if not provided
    def save(self, *args, **kwargs):
        if self.endpoint_slug is None or self.endpoint_slug == "":
            self.endpoint_slug = slugify(" ".join([self.cluster, self.framework, self.model]))
        super(Endpoint, self).save(*args, **kwargs)

 
# Log of Globus Compute requests sent to Globus
class Log(models.Model):

    # User who triggered a Globus task
    name = models.CharField(max_length=100)
    username = models.CharField(max_length=100)

    # Requested resource and model
    endpoint_slug = models.SlugField(null=True, max_length=100)

    # Requested openai_endpoint
    openai_endpoint = models.CharField(null=True, max_length=100)

    # Prompt requested by the user
    # TODO: Should we add all the other parameters?
    prompt = models.TextField(null=True)

    # Globus Compute task UUID
    task_uuid = models.CharField(null=True, max_length=100)

    # Whether the request is synchronous
    # If True, the view waited for the compute results
    # If False, the view returns the compute task UUID
    # TODO: This is not needed anymore
    sync = models.BooleanField()

    # Time when the HTTP request was received (after the auth checks)
    timestamp_receive = models.DateTimeField(null=True, blank=False)

    # Time when the Globus compute request was submitted
    timestamp_submit = models.DateTimeField(null=True, blank=False)

    # Time when the response was sent back to the user
    timestamp_response = models.DateTimeField(null=True, blank=True)

    # Response status code sent back to the user
    response_status = models.IntegerField(null=True)

    # Inference raw result or error messages if response status code is not 200
    result = models.TextField(null=True)

    # String function
    def __str__(self):
        return f"<{self.username} - {self.timestamp_receive} - {self.endpoint_slug}>"
    

# Log of list-endpoints requests, which may include Globus Compute qstat tasks
class ListEndpointsLog(models.Model):
    # NOTE: all lists are in sync with each other in terms of indices

    # User who triggered a Globus task
    name = models.CharField(max_length=100)
    username = models.CharField(max_length=100)

    # List (string separated by ";") of endpoint slugs targetted by the user
    endpoint_slugs = models.TextField(default="", blank=True)

    # List (string separated by ";") of Globus Compute task UUIDs triggered by the user
    task_uuids = models.TextField(default="", blank=True)

    # Time when the HTTP request was received (after the auth checks)
    timestamp_receive = models.DateTimeField(null=True, blank=False)

    # Time when the response was sent back to the user
    timestamp_response = models.DateTimeField(null=True, blank=True)

    # Response status code sent back to the user
    response_status = models.IntegerField(null=True)

    # Error message if any
    error_message = models.TextField(default="", blank=True)

    # String function
    def __str__(self):
        return f"<{self.username} - {self.timestamp_receive} - {self.response_status}>"


# Log for batch requests
class Batch(models.Model):

    # Unique UUID assigned to the batch request
    batch_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    # Who submitted the batch?
    name = models.CharField(max_length=100)
    username = models.CharField(max_length=100)

    # What did the user request?
    input_file = models.CharField(max_length=500)
    output_folder_path = models.CharField(max_length=500, blank=True)
    cluster = models.CharField(max_length=100)
    framework = models.CharField(max_length=100)
    model = models.CharField(max_length=250)
    # OpenAI extra fields
    #metadata = models.JSONField(default=dict)
    #completion_window = models.CharField(max_length=100)
    #endpoint = models.CharField(max_length=250)

    # List of Globus task UUIDs tied to the batch (string separated with ,)
    globus_batch_uuid = models.CharField(max_length=100)
    globus_task_uuids = models.TextField(null=True)
    result = models.TextField(blank=True)
    error = models.TextField(blank=True)

    # What is the status of the batch?
    status = models.CharField(max_length=250, default="pending")
    created_at = models.DateTimeField(default=now)
    in_progress_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    failed_at = models.DateTimeField(null=True, blank=True)
    # OpenAI extra fields
    #output_file_id = models.CharField(max_length=250, blank=True)
    #error_file_id = models.CharField(max_length=250, blank=True)
    #expires_at = models.DateTimeField(null=True, blank=True)
    #finalizing_at = models.DateTimeField(null=True, blank=True)
    #object = models.CharField(max_length=100, default="batch")
    #errors = models.JSONField(default=dict)
    #expired_at = models.DateTimeField(null=True, blank=True)
    #cancelling_at = models.DateTimeField(null=True, blank=True)
    #cancelled_at = models.DateTimeField(null=True, blank=True)
    #request_counts = models.JSONField(default=dict)

    # String function
    def __str__(self):
        return f"Batch - <{self.username} - {self.created_at}>"


# Represents a federated model accessible via a single name,
# potentially served by multiple concrete endpoints.
class FederatedEndpoint(models.Model):

    # User-friendly name for the federated model offering
    name = models.CharField(max_length=200)

    # Unique slug for internal use and potentially URLs
    slug = models.SlugField(max_length=250, unique=True)

    # The canonical model name that users will request (e.g., 'meta-llama/Meta-Llama-3.1-8B-Instruct')
    # Should match the 'model' field in the user's request body.
    # Indexing for faster lookups.
    target_model_name = models.CharField(max_length=250, unique=True, db_index=True)

    # Store the list of potential concrete targets directly as JSON.
    # Each item in the list should be a dictionary containing:
    # { 
    #   "cluster": "cluster_name", 
    #   "framework": "framework_name", 
    #   "endpoint_uuid": "uuid", 
    #   "function_uuid": "uuid", 
    #   "api_port": port_number, 
    #   "allowed_globus_groups": "group_str" (optional) 
    # }
    targets = models.JSONField(default=list)

    # Description (optional)
    description = models.TextField(blank=True, default="")

    def __str__(self):
        return f"<FederatedEndpoint {self.slug} ({self.target_model_name})>"

    # Automatically generate slug if not provided
    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)


# Details of the latest jobs/ URL (qstat function) status
class ModelStatus(models.Model):

    # Targetted cluster where the models run
    cluster = models.CharField(max_length=128, unique=True)

    # Full raw response from the qstat function
    result = models.TextField(default="", blank=True)

    # Error message if something goes wrong
    error = models.TextField(default="", blank=True)

    # Timestamp to keep track of the last qstat query
    timestamp = models.DateTimeField(default=now)


# Log for file path imports
#class File(models.Model):
#
#    # Unique UUID assigned to the input file path request
#    input_file_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
#
#    # Input file path on the HPC resource
#    input_file_path = models.TextField(blank=False, null=False)
#    
#    # Info on who submited the file path
#    name = models.CharField(max_length=100)
#    username = models.CharField(max_length=100)
#
#    # Timestamps
#    created_at = models.DateTimeField(default=now)
#
#    # String function
#    def __str__(self):
#        return f"Batch - <{self.username} - {self.created_at}>"