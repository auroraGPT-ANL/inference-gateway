from django.core.management.base import BaseCommand, CommandError
from resource_server.models import Batch
from resource_server_async.utils import update_batch_status_result
from asgiref.sync import async_to_sync

# Django management command
class Command(BaseCommand):
    help = "Go through all Batch database objects and update status/result if needed."

    # Management command definition
    def handle(self, *args, **kwargs):

        # Print signs of execution
        print("update_batch_status management command executed")

        # Gather all batch objects that are still in progress
        try:
            pending_running_batches = Batch.objects.filter(status__in=['pending', 'running'])
        except Exception as e:
            raise CommandError(f"Error: Could not extract in_progress batches from database: {e}")

        # Update the status of each batch and collect result if available
        for batch in pending_running_batches:
            try:
                batch_status_before = batch.status
                batch_status, _, _, _ = async_to_sync(update_batch_status_result)(batch, cross_check=True)
                print(f"batch {batch.batch_id} updated from {batch_status_before} to {batch_status}.")
            except Exception as e:
                pass
