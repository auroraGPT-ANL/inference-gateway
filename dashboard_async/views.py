from datetime import timedelta
from django.db.models import Count, Avg, F, Q, Subquery, OuterRef, Value, CharField
from django.db.models.functions import TruncWeek, TruncDay
from django.utils.timezone import now
from ninja import NinjaAPI, Router
from resource_server.models import Endpoint, Log, Batch
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
        # 1) Overall stats from mv_overall_stats
        cursor.execute("SELECT * FROM mv_overall_stats")
        row = cursor.fetchone()
        total_requests, successful_requests, failed_requests, total_users = row

        # 2) User details from mv_user_details
        cursor.execute("SELECT name, username FROM mv_user_details")
        user_details = [{"name": row[0], "username": row[1]} for row in cursor.fetchall()]

        # 3) Model-specific metrics from mv_model_requests
        cursor.execute("SELECT * FROM mv_model_requests")
        model_requests = [
            {
                "model": row[0],
                "total_requests": row[1],
                "successful_requests": row[2],
                "failed_requests": row[3]
            }
            for row in cursor.fetchall()
        ]

        # 4) Model latency from mv_model_latency
        cursor.execute("SELECT * FROM mv_model_latency")
        model_latency = [
            {"model": row[0], "avg_latency": row[1]}
            for row in cursor.fetchall()
        ]

        # 5) Users per model from mv_users_per_model
        cursor.execute("SELECT * FROM mv_users_per_model")
        users_per_model = [
            {"model": row[0], "user_count": row[1]}
            for row in cursor.fetchall()
        ]

        # 6) Weekly usage from mv_weekly_usage
        cursor.execute("SELECT * FROM mv_weekly_usage")
        weekly_usage = [
            {"week_start": row[0], "request_count": row[1]}
            for row in cursor.fetchall()
        ]

        # 7) Daily usage from mv_daily_usage_2_weeks
        cursor.execute("SELECT * FROM mv_daily_usage_2_weeks")
        daily_usage_2_weeks = [
            {"day": row[0], "request_count": row[1]}
            for row in cursor.fetchall()
        ]

        # 8) Model throughput from mv_model_throughput
        cursor.execute("SELECT * FROM mv_model_throughput")
        model_throughput = [
            {"model": row[0], "avg_throughput": row[1]}
            for row in cursor.fetchall()
        ]

        # 9) Requests per user from mv_requests_per_user
        cursor.execute("SELECT * FROM mv_requests_per_user")
        requests_per_user = [
            {
                "name": row[0],
                "username": row[1],
                "total_requests": row[2],
                "successful_requests": row[3],
                "failed_requests": row[4]
            }
            for row in cursor.fetchall()
        ]

        # 10) Batch Overview
        cursor.execute("SELECT * FROM mv_batch_total_jobs")
        row = cursor.fetchone()
        batch_overview = {
            "total_batch_jobs": row[0],
            "completed_batch_jobs": row[1],
            "failed_batch_jobs": row[2],
            "pending_batch_jobs": row[3],
            "running_batch_jobs": row[4]
        }
        
        # 11) Batch Successful Requests
        cursor.execute("SELECT * FROM mv_batch_successful_requests")
        row = cursor.fetchone()
        batch_successful_requests = row[0] if row[0] is not None else 0

        # 12) Batch Requests Per Model
        cursor.execute("SELECT * FROM mv_batch_requests_per_model")
        batch_requests_per_model = [
            {
                "model": row[0],
                "total_jobs": row[1],
                "completed_jobs": row[2],
                "total_requests": row[3]
            }
            for row in cursor.fetchall()
        ]

        # 13) Batch Unique Users
        cursor.execute("SELECT * FROM mv_batch_unique_users")
        row = cursor.fetchone()
        batch_unique_users = row[0] if row is not None else 0

        # 14) Batch Total Tokens
        cursor.execute("SELECT * FROM mv_batch_total_tokens")
        row = cursor.fetchone()
        batch_total_tokens = row[0] if row[0] is not None else 0

        # 15) Batch Average Latency
        cursor.execute("SELECT * FROM mv_batch_avg_latency")
        batch_avg_latency = [
            {"model": row[0], "avg_response_time_sec": row[1]}
            for row in cursor.fetchall()
        ]

        # 16) Batch Average Throughput
        cursor.execute("SELECT * FROM mv_batch_avg_throughput")
        batch_avg_throughput = [
            {"model": row[0], "avg_throughput_tokens_per_sec": row[1]}
            for row in cursor.fetchall()
        ]

        # 17) Add daily batch usage
        cursor.execute("SELECT * FROM mv_batch_daily_usage")
        batch_daily_usage = [
            {
                "day": row[0],
                "batch_count": row[1],
                "completed_count": row[2],
                "failed_count": row[3],
                "total_requests": row[4]
            }
            for row in cursor.fetchall()
        ]


    
    # Calculate today's RPS (still needs real-time data)
    day_start = now().replace(hour=0, minute=0, second=0, microsecond=0)
    requests_today = Log.objects.filter(timestamp_receive__gte=day_start).count()
    time_since_day_start = (now() - day_start).total_seconds()
    average_rps_today = requests_today / time_since_day_start if time_since_day_start > 0 else 0

    # Calculate daily RPS for past 7 days
    daily_rps_7_days = []
    for day_info in daily_usage_2_weeks:
        day_dt = day_info["day"]
        req_count = day_info["request_count"]

        if day_dt.date() == now().date():
            partial_seconds = (now() - day_start).total_seconds()
            avg_rps = req_count / partial_seconds if partial_seconds > 0 else 0
        else:
            avg_rps = req_count / 86400.0

        daily_rps_7_days.append({
            "day": day_dt.isoformat(),
            "average_rps": avg_rps
        })

    return {
        "total_requests": total_requests,
        "request_details": {
            "successful": successful_requests,
            "failed": failed_requests,
        },
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
        
        # New batch metrics
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
def get_metrics(request):
    """Get dashboard metrics (synchronously)."""
    try:
        log.info("Fetching metrics (sync)...")
        metrics = fetch_metrics()
        return metrics
    except Exception as e:
        log.error(f"Error fetching metrics: {e}")
        return {"error": str(e)}, 500


@router.get("/endpoints")
def get_endpoints(request):
    """Fetch active endpoints (synchronously)."""
    try:
        endpoints = list(Endpoint.objects.all())
        endpoint_details = []
        for endpoint in endpoints:
            endpoint_details.append(
                {
                    "model": endpoint.model,
                    "cluster": endpoint.cluster,
                    "framework": endpoint.framework,
                }
            )
        return {"endpoints": endpoint_details}
    except Exception as e:
        log.error(f"Error fetching endpoints: {e}")
        return {"error": str(e)}, 500


@router.get("/analytics")
def analytics_view(request):
    """Render the analytics dashboard (sync)."""
    from django.shortcuts import render
    return render(request, "analytics.html")


# Logs endpoint (paginated sync)
@router.get("/logs")
def get_logs(request, page: int = 0, per_page: int = 100):
    try:
        from django.db.models.functions import Coalesce
        subquery_model = Subquery(
            Endpoint.objects.filter(endpoint_slug=OuterRef("endpoint_slug")).values("model")[:1]
        )

        # Query logs
        logs_query = (Log.objects
            .annotate(
                model=Coalesce(subquery_model, Value("")),
                latency=F("timestamp_response") - F("timestamp_receive"),
            )
            .filter(response_status__isnull=False)
            .order_by("-timestamp_receive")
        )

        start_index = page * per_page
        end_index = start_index + per_page
        paginated_logs = logs_query[start_index:end_index]

        # Convert to list of dict
        results = []
        for logobj in paginated_logs:
            latency_secs = None
            if logobj.latency:
                latency_secs = logobj.latency.total_seconds()
            results.append({
                "name": logobj.name,
                "username": logobj.username,
                "model": logobj.model,
                "response_status": logobj.response_status,
                "latency": latency_secs,
            })
        return results
    except Exception as e:
        log.error(f"Error fetching logs (sync): {e}")
        return {"error": str(e)}, 500


# Add this function to fetch batch logs (paginated)
@router.get("/batch-logs")
def get_batch_logs(request, page: int = 0, per_page: int = 100):
    try:
        # Query batch logs
        batch_logs_query = (Batch.objects
            .values('batch_id', 'name', 'username', 'model', 'status', 'created_at', 'completed_at')
            .order_by('-created_at')
        )

        start_index = page * per_page
        end_index = start_index + per_page
        paginated_logs = batch_logs_query[start_index:end_index]

        # Convert to list of dict
        results = []
        for batch in paginated_logs:
            # Calculate duration if completed
            duration = None
            if batch['completed_at'] and batch['created_at']:
                duration = (batch['completed_at'] - batch['created_at']).total_seconds()
            
            results.append({
                "batch_id": str(batch['batch_id']),
                "name": batch['name'],
                "username": batch['username'],
                "model": batch['model'],
                "status": batch['status'],
                "created_at": batch['created_at'].isoformat() if batch['created_at'] else None,
                "duration": duration
            })
        return results
    except Exception as e:
        log.error(f"Error fetching batch logs (sync): {e}")
        return {"error": str(e)}, 500


api.add_router("/", router)
