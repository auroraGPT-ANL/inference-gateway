
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from .models import Endpoint
from utils.cache import invalidate_cache
from resource_server_async.utils import get_all_endpoints_from_cache, get_endpoint_by_slug_from_cache
import logging

log = logging.getLogger(__name__)

@receiver(post_save, sender=Endpoint)
def invalidate_endpoint_cache_on_save(sender, instance, **kwargs):
    """
    Invalidates cache for get_all_endpoints_from_cache and
    get_endpoint_by_slug_from_cache when an Endpoint is saved.
    """
    log.info(f"Signal received: Endpoint {instance.endpoint_slug} saved. Invalidating cache.")
    invalidate_cache(get_all_endpoints_from_cache)
    invalidate_cache(get_endpoint_by_slug_from_cache, instance.endpoint_slug)

@receiver(post_delete, sender=Endpoint)
def invalidate_endpoint_cache_on_delete(sender, instance, **kwargs):
    """
    Invalidates cache for get_all_endpoints_from_cache and
    get_endpoint_by_slug_from_cache when an Endpoint is deleted.
    """
    log.info(f"Signal received: Endpoint {instance.endpoint_slug} deleted. Invalidating cache.")
    invalidate_cache(get_all_endpoints_from_cache)
    invalidate_cache(get_endpoint_by_slug_from_cache, instance.endpoint_slug) 