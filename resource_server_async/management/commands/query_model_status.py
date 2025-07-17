from django.core.management.base import BaseCommand, CommandError
from resource_server.models import ModelStatus
from resource_server_async.utils import get_qstat_details
from utils.globus_utils import get_compute_client_from_globus_app, get_compute_executor
from asgiref.sync import async_to_sync
from django.conf import settings

# Django management command
class Command(BaseCommand):
    help = "Query the qstat function and record results in the Postgres database."

    # Management command definition
    def handle(self, *args, **kwargs):

        # Print signs of execution
        print("query_model_status management command executed")

        # Clear database
        ModelStatus.objects.all().delete()

        # For each cluster ...
        for cluster in settings.ALLOWED_QSTAT_ENDPOINTS:
            print(f"Treating cluster {cluster}")

            # Create a new database entry for that cluster
            model = ModelStatus(cluster=cluster, result="", error="")

            # Create Globus Compute client and executor
            try:
                gcc = get_compute_client_from_globus_app()
                gce = get_compute_executor(client=gcc, amqp_port=443)
            except Exception as e:
                error_message = f"Could not create Globus Compute client or executor: {e}"
                model.result = ""
                model.error = error_message
                model.save()
                raise CommandError(error_message)
        
            # Try to collect the qstat details
            try:
                result, _, error, _  = async_to_sync(get_qstat_details)(cluster, gcc, gce, timeout=60)
            except Exception as e:
                error_message = f"Could not extract model status: {e}"
                model.result = ""
                model.error = error_message
                model.save()
                raise CommandError(error_message)

            # Add result or error in the database
            if len(error) > 0:
                model.result = ""
                model.error = error
            elif len(result) > 0:
                model.result = result
                model.error = ""
            model.save()
