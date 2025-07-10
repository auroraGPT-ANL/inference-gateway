from datetime import timedelta
from django.db.models import Count, Avg, F, Q, Subquery, OuterRef, Value, CharField
from django.db.models.functions import TruncWeek, TruncDay
from django.utils.timezone import now
from django.http import HttpResponse
from django.core.cache import cache
from django.template.loader import get_template
from django.views.decorators.cache import cache_page
from asgiref.sync import sync_to_async
from ninja import NinjaAPI, Router
from resource_server.models import Endpoint, Log, Batch
from resource_server_async.utils import get_all_endpoints_from_cache
import re
import logging

log = logging.getLogger(__name__)

api = NinjaAPI(urls_namespace="dashboard_api")
router = Router()

# Regex for throughput_tokens_per_second in JSON-like or partial text
THROUGHPUT_PATTERN = re.compile(r'[\"\']throughput_tokens_per_second[\"\']\s*:\s*([\d\.]+)')

def fetch_metrics():
    """Fetch metrics synchronously using materialized views."""
    from django.db import connection

    with connection.cursor() as cursor:
        # This function remains synchronous, but will be called in an async context.
        # This is more efficient than making each of the 17+ queries async individually.
        cursor.execute("SELECT * FROM mv_overall_stats")
        row = cursor.fetchone()
        total_requests, successful_requests, failed_requests, total_users = (row if row else (0, 0, 0, 0))

        cursor.execute("SELECT name, username FROM mv_user_details")
        user_details = [{"name": row[0], "username": row[1]} for row in cursor.fetchall()]

        cursor.execute("SELECT * FROM mv_model_requests")
        model_requests = [{"model": r[0], "total_requests": r[1], "successful_requests": r[2], "failed_requests": r[3]} for r in cursor.fetchall()]

        cursor.execute("SELECT * FROM mv_model_latency")
        model_latency = [{"model": r[0], "avg_latency": r[1]} for r in cursor.fetchall()]

        cursor.execute("SELECT * FROM mv_users_per_model")
        users_per_model = [{"model": r[0], "user_count": r[1]} for r in cursor.fetchall()]

        cursor.execute("SELECT * FROM mv_weekly_usage")
        weekly_usage = [{"week_start": r[0], "request_count": r[1]} for r in cursor.fetchall()]

        cursor.execute("SELECT * FROM mv_daily_usage_2_weeks")
        daily_usage_2_weeks = [{"day": r[0], "request_count": r[1]} for r in cursor.fetchall()]

        cursor.execute("SELECT * FROM mv_model_throughput")
        model_throughput = [{"model": r[0], "avg_throughput": r[1]} for r in cursor.fetchall()]

        cursor.execute("SELECT * FROM mv_requests_per_user")
        requests_per_user = [{"name": r[0], "username": r[1], "total_requests": r[2], "successful_requests": r[3], "failed_requests": r[4]} for r in cursor.fetchall()]

        cursor.execute("SELECT * FROM mv_batch_total_jobs")
        row = cursor.fetchone()
        batch_overview = {"total_batch_jobs": row[0], "completed_batch_jobs": row[1], "failed_batch_jobs": row[2], "pending_batch_jobs": row[3], "running_batch_jobs": row[4]} if row else {}

        cursor.execute("SELECT * FROM mv_batch_successful_requests")
        row = cursor.fetchone()
        batch_successful_requests = row[0] if row and row[0] is not None else 0

        cursor.execute("SELECT * FROM mv_batch_requests_per_model")
        batch_requests_per_model = [{"model": r[0], "total_jobs": r[1], "completed_jobs": r[2], "total_requests": r[3]} for r in cursor.fetchall()]

        cursor.execute("SELECT * FROM mv_batch_unique_users")
        row = cursor.fetchone()
        batch_unique_users = row[0] if row else 0

        cursor.execute("SELECT * FROM mv_batch_total_tokens")
        row = cursor.fetchone()
        batch_total_tokens = row[0] if row and row[0] is not None else 0

        cursor.execute("SELECT * FROM mv_batch_avg_latency")
        batch_avg_latency = [{"model": r[0], "avg_response_time_sec": r[1]} for r in cursor.fetchall()]

        cursor.execute("SELECT * FROM mv_batch_avg_throughput")
        batch_avg_throughput = [{"model": r[0], "avg_throughput_tokens_per_sec": r[1]} for r in cursor.fetchall()]

        cursor.execute("SELECT * FROM mv_batch_daily_usage")
        batch_daily_usage = [{"day": r[0], "batch_count": r[1], "completed_count": r[2], "failed_count": r[3], "total_requests": r[4]} for r in cursor.fetchall()]

    day_start = now().replace(hour=0, minute=0, second=0, microsecond=0)
    requests_today = Log.objects.filter(timestamp_receive__gte=day_start).count()
    time_since_day_start = (now() - day_start).total_seconds()
    average_rps_today = requests_today / time_since_day_start if time_since_day_start > 0 else 0

    daily_rps_7_days = []
    for day_info in daily_usage_2_weeks:
        day_dt = day_info["day"]
        req_count = day_info["request_count"]
        avg_rps = req_count / 86400.0 if day_dt.date() != now().date() else (req_count / time_since_day_start if time_since_day_start > 0 else 0)
        daily_rps_7_days.append({"day": day_dt.isoformat(), "average_rps": avg_rps})

    return {
        "total_requests": total_requests,
        "request_details": {"successful": successful_requests, "failed": failed_requests},
        "total_users": total_users,
        "user_details": user_details,
        "model_requests": model_requests,
        "model_latency": model_latency,
        "users_per_model": users_per_model,
        "weekly_usage_2_months": weekly_usage,
        "daily_usage_2_weeks": daily_usage_2_weeks,
        "average_rps_today": average_rps_today,
        "daily_rps_7_days": daily_rps_7_days,
        "model_throughput": model_throughput,
        "requests_per_user": requests_per_user,
        "batch_overview": batch_overview,
        "batch_successful_requests": batch_successful_requests,
        "batch_requests_per_model": batch_requests_per_model,
        "batch_unique_users": batch_unique_users,
        "batch_total_tokens": batch_total_tokens,
        "batch_avg_latency": batch_avg_latency,
        "batch_avg_throughput": batch_avg_throughput,
        "batch_daily_usage": batch_daily_usage,
    }


@router.get("/metrics")
async def get_metrics(request):
    """Get dashboard metrics. Caches results for 60 seconds."""
    cache_key = "dashboard_metrics"
    metrics = cache.get(cache_key)
    if metrics:
        log.info("Fetching metrics from cache...")
        return metrics

    try:
        log.info("Fetching metrics from DB (async)...")
        metrics = await sync_to_async(fetch_metrics, thread_sensitive=True)()
        cache.set(cache_key, metrics, 60)  # Cache for 60 seconds
        return metrics
    except Exception as e:
        log.error(f"Error fetching metrics: {e}")
        return {"error": str(e)}, 500


@router.get("/endpoints")
async def get_endpoints(request):
    """Fetch active endpoints asynchronously."""
    try:
        endpoints = await get_all_endpoints_from_cache()
        endpoint_details = [
            {"model": ep.model, "cluster": ep.cluster, "framework": ep.framework}
            for ep in endpoints
        ]
        return {"endpoints": endpoint_details}
    except Exception as e:
        log.error(f"Error fetching endpoints: {e}")
        return {"error": str(e)}, 500


@router.get("/analytics")
@cache_page(60 * 2)  # Cache for 2 minutes
async def analytics_view(request):
    """Render the analytics dashboard asynchronously."""
    try:
        template = get_template("analytics.html")
        content = await sync_to_async(template.render)(context={}, request=request)
        return HttpResponse(content)
    except Exception as e:
        log.error(f"Error rendering analytics view: {e}")
        return HttpResponse("Error rendering page.", status=500)


# Logs endpoint (paginated async)
@router.get("/logs")
async def get_logs(request, page: int = 0, per_page: int = 100):
    """Fetch logs asynchronously with pagination."""
    try:
        from django.db.models.functions import Coalesce
        subquery_model = Subquery(
            Endpoint.objects.filter(endpoint_slug=OuterRef("endpoint_slug")).values("model")[:1]
        )

        logs_query = (Log.objects
            .annotate(model=Coalesce(subquery_model, Value("")), latency=F("timestamp_response") - F("timestamp_receive"))
            .filter(response_status__isnull=False)
            .order_by("-timestamp_receive")
        )

        start_index = page * per_page
        end_index = start_index + per_page
        
        paginated_logs_sync = logs_query[start_index:end_index]
        paginated_logs = await sync_to_async(list)(paginated_logs_sync)

        results = [
            {
                "name": logobj.name, "username": logobj.username, "model": logobj.model,
                "response_status": logobj.response_status,
                "latency": logobj.latency.total_seconds() if logobj.latency else None,
            }
            for logobj in paginated_logs
        ]
        return results
    except Exception as e:
        log.error(f"Error fetching logs (async): {e}")
        return {"error": str(e)}, 500


@router.get("/batch-logs")
async def get_batch_logs(request, page: int = 0, per_page: int = 100):
    """Fetch batch logs asynchronously with pagination."""
    try:
        batch_logs_query = (Batch.objects
            .values('batch_id', 'name', 'username', 'model', 'status', 'created_at', 'completed_at')
            .order_by('-created_at')
        )

        start_index = page * per_page
        end_index = start_index + per_page
        
        paginated_logs_sync = batch_logs_query[start_index:end_index]
        paginated_logs = await sync_to_async(list)(paginated_logs_sync)

        results = [
            {
                "batch_id": str(batch['batch_id']), "name": batch['name'], "username": batch['username'],
                "model": batch['model'], "status": batch['status'],
                "created_at": batch['created_at'].isoformat() if batch['created_at'] else None,
                "duration": (batch['completed_at'] - batch['created_at']).total_seconds() if batch['completed_at'] and batch['created_at'] else None
            }
            for batch in paginated_logs
        ]
        return results
    except Exception as e:
        log.error(f"Error fetching batch logs (async): {e}")
        return {"error": str(e)}, 500


api.add_router("/", router)
