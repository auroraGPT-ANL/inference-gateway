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
    cluster = models.CharField(max_length=100)
    framework = models.CharField(max_length=100)
    model = models.CharField(max_length=100)

    # Prompt requested by the user
    # TODO: Should we add all the other parameters?
    prompt = models.TextField()

    # Globus Compute task UUID
    task_uuid = models.CharField(max_length=100, unique=True)

    # Whether the request is synchronous
    # If True, the view waited for the compute results
    # If False, the view returns the compute task UUID
    sync = models.BooleanField()

    # Whether the request was completed
    # In sync mode, True means the Globus task is not pending anymore
    # In async mode, True means the Globus compute task uuid was collected
    completed = models.BooleanField(default=False)

    # String function
    def __str__(self):
        return f"<Task UUID {self.model} - {self.username} - ({self.completed})>"
    