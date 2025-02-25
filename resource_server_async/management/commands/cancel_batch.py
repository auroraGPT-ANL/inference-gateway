from django.core.management.base import BaseCommand, CommandError
from resource_server.models import Batch
from asgiref.sync import async_to_sync
from resource_server_async.utils import kill_HPC_batch_job

# Django management command
class Command(BaseCommand):
    help = "Go through all Batch database objects and attempt to cancel them if needed."

    # Management command definition
    def handle(self, *args, **kwargs):

        # Print signs of execution
        print("cancel_batch management command executed.")

        # Gather all batch objects that are scheduled to be cancelled
        try:
            cancelling_batches = Batch.objects.filter(status="cancelling")
        except Exception as e:
            raise CommandError(f"Error: Could not extract cancelling batches from database: {e}")
        
        # Attempt to cancel each batch and kill HPC job if possible
        for batch in cancelling_batches:
            try:
                batch_status_before = batch.status
                batch_status, _, _, _ = async_to_sync(kill_HPC_batch_job)(batch)
                print(f"batch {batch.batch_id} updated from {batch_status_before} to {batch_status}.")
            except Exception as e:
                pass
