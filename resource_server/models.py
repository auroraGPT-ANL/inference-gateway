from django.db import models
from django.utils.text import slugify

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

    # Globus Compute endpoint UUID
    endpoint_uuid = models.CharField(max_length=100)

    # Globus Compute function UUID
    function_uuid = models.CharField(max_length=100)

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
    sync = models.BooleanField()

    # Time when the HTTP request was received (before the auth checks)
    timestamp_receive = models.DateTimeField(null=True, blank=False)

    # Time when the Globus compute request was submitted (after the auth checks)
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
    
