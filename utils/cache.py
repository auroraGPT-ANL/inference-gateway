import functools
from django.core.cache import cache
from asgiref.sync import sync_to_async
import logging

log = logging.getLogger(__name__)

def _generate_cache_key(func, *args, **kwargs):
    # Create a cache key based on the function name and arguments
    key_args = [str(arg) for arg in args]
    key_kwargs = [f"{k}={v}" for k, v in sorted(kwargs.items())]
    return f"{func.__name__}:{':'.join(key_args)}:{':'.join(key_kwargs)}"

def redis_cache(ttl, *, validator=lambda r: r is not None):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            cache_key = _generate_cache_key(func, *args, **kwargs)
            cached_result = cache.get(cache_key)
            if cached_result is not None:
                return cached_result
            
            result = func(*args, **kwargs)
            
            if validator(result):
                cache.set(cache_key, result, timeout=ttl)
            
            return result
        return wrapper
    return decorator

def async_redis_cache(ttl, *, validator=lambda r: r is not None):
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            cache_key = _generate_cache_key(func, *args, **kwargs)
            
            cached_result = await sync_to_async(cache.get)(cache_key)
            if cached_result is not None:
                return cached_result
                
            result = await func(*args, **kwargs)
            
            if validator(result):
                await sync_to_async(cache.set)(cache_key, result, timeout=ttl)
            
            return result
        return wrapper
    return decorator

def invalidate_cache(func, *args, **kwargs):
    """Manually invalidates a cache entry for a given function and arguments."""
    try:
        cache_key = _generate_cache_key(func, *args, **kwargs)
        cache.delete(cache_key)
        log.info(f"Invalidated cache for key: {cache_key}")
    except Exception as e:
        log.error(f"Error invalidating cache for {func.__name__}: {e}")

async def async_invalidate_cache(func, *args, **kwargs):
    """Asynchronously invalidates a cache entry."""
    try:
        cache_key = _generate_cache_key(func, *args, **kwargs)
        await sync_to_async(cache.delete)(cache_key)
        log.info(f"Invalidated cache for key: {cache_key}")
    except Exception as e:
        log.error(f"Error invalidating cache for {func.__name__}: {e}") 