from django.utils.timezone import now
from datetime import timedelta, datetime
from asgiref.sync import sync_to_async
from ninja import NinjaAPI, Router, Schema
from resource_server.models import Endpoint, Log
from django.db.models import Count, Avg, F, Q, Subquery, OuterRef, Value, CharField
from django.db.models.functions import Coalesce, TruncWeek, TruncDay
from django.shortcuts import render
from typing import List
import re

import logging
log = logging.getLogger(__name__)

api = NinjaAPI(urls_namespace="dashboard_async_api")
router = Router()

# Regex to capture numeric throughput_tokens_per_second, e.g. 123.45
THROUGHPUT_PATTERN = re.compile(r'[\"\']throughput_tokens_per_second[\"\']\s*:\s*([\d\.]+)')

class LogResponseSchema(Schema):
    name: str
    username: str
    model: str
    response_status: int
    latency: float

# Utility Function to Fetch Metrics
async def fetch_metrics():
    """Fetch metrics asynchronously with new features."""
    # ------------------------------------------------------
    # Overall metrics
    # ------------------------------------------------------
    total_requests = await sync_to_async(Log.objects.count)()
    successful_requests = await sync_to_async(
        Log.objects.filter(Q(response_status=200) | Q(response_status__isnull=True)).count
    )()
    failed_requests = total_requests - successful_requests

    total_users = await sync_to_async(
        Log.objects.values("username").distinct().count
    )()
    user_details = await sync_to_async(
        lambda: list(Log.objects.values("name", "username").distinct())
    )()

    # ------------------------------------------------------
    # Annotate logs with model info
    # ------------------------------------------------------
    endpoint_models = Endpoint.objects.filter(
        endpoint_slug=OuterRef("endpoint_slug")
    ).values("model")[:1]

    logs_with_annotations = await sync_to_async(
        lambda: Log.objects.annotate(
            model=Subquery(endpoint_models, output_field=CharField()),
        )
    )()

    # Filter out logs missing a model (null or empty)
    logs_with_model = logs_with_annotations.filter(model__isnull=False).exclude(model="")

    # ------------------------------------------------------
    # Model-Specific Metrics
    # ------------------------------------------------------
    # Requests per model
    model_requests = await sync_to_async(
        lambda: list(
            logs_with_model.values("model").annotate(
                total_requests=Count("id"),
                successful_requests=Count(
                    "id", filter=Q(response_status=200) | Q(response_status__isnull=True)
                ),
                failed_requests=Count(
                    "id", filter=~Q(response_status=200) & Q(response_status__isnull=False)
                ),
            )
        )
    )()

    # Average latency per model (only for logs_with_model)
    model_latency = await sync_to_async(
        lambda: list(
            logs_with_model.filter(Q(response_status=200) | Q(response_status__isnull=True))
            .values("model")
            .annotate(avg_latency=Avg(F("timestamp_response") - F("timestamp_receive")))
        )
    )()

    # Users per model
    users_per_model = await sync_to_async(
        lambda: list(
            logs_with_model.values("model").annotate(
                user_count=Count("username", distinct=True)
            )
        )
    )()

    # ------------------------------------------------------
    # Requests per user
    # ------------------------------------------------------
    requests_per_user = await sync_to_async(
        lambda: list(
            logs_with_annotations.values("name").annotate(
                total_requests=Count("id"),
                successful_requests=Count(
                    "id", filter=Q(response_status=200) | Q(response_status__isnull=True)
                ),
                failed_requests=Count(
                    "id", filter=~Q(response_status=200) & Q(response_status__isnull=False)
                ),
            )
        )
    )()

    # ------------------------------------------------------
    # Weekly Usage (Past 2 Months) - Already in your code
    # ------------------------------------------------------
    two_months_ago = now() - timedelta(days=60)
    weekly_usage_2_months = await sync_to_async(
        lambda: list(
            Log.objects.filter(timestamp_receive__gte=two_months_ago)
            .annotate(week_start=TruncWeek("timestamp_receive"))
            .values("week_start")
            .annotate(request_count=Count("id"))
            .order_by("week_start")
        )
    )()

    # ------------------------------------------------------
    # Daily Usage (Past 2 Weeks) - Already in your code
    # ------------------------------------------------------
    two_weeks_ago = now() - timedelta(days=14)
    daily_usage_2_weeks = await sync_to_async(
        lambda: list(
            Log.objects.filter(timestamp_receive__gte=two_weeks_ago)
            .annotate(day=TruncDay("timestamp_receive"))
            .values("day")
            .annotate(request_count=Count("id"))
            .order_by("day")
        )
    )()

    # ------------------------------------------------------
    # Running average RPS for Today (unchanged)
    # ------------------------------------------------------
    day_start = now().replace(hour=0, minute=0, second=0, microsecond=0)
    requests_today = await sync_to_async(
        Log.objects.filter(timestamp_receive__gte=day_start).count
    )()
    time_since_day_start = (now() - day_start).total_seconds()
    average_rps_today = (
        requests_today / time_since_day_start if time_since_day_start > 0 else 0
    )

    # ------------------------------------------------------
    # (New) Average RPS per day for the last 7 days
    # ------------------------------------------------------
    seven_days_ago = now() - timedelta(days=7)
    daily_counts_7_days = await sync_to_async(
        lambda: list(
            Log.objects.filter(timestamp_receive__gte=seven_days_ago)
            .annotate(day=TruncDay("timestamp_receive"))
            .values("day")
            .annotate(request_count=Count("id"))
            .order_by("day")
        )
    )()

    # For each day, compute "average RPS" = request_count / (86400 or partial day if day == today)
    daily_rps_7_days = []
    for day_info in daily_counts_7_days:
        day_dt = day_info["day"]
        req_count = day_info["request_count"]

        # Compare with today's date
        if day_dt.date() == now().date():
            # partial day
            partial_seconds = (now() - day_start).total_seconds()
            if partial_seconds > 0:
                avg_rps = req_count / partial_seconds
            else:
                avg_rps = 0
        else:
            # full day
            avg_rps = req_count / 86400.0

        daily_rps_7_days.append({
            "day": day_dt.isoformat(),
            "average_rps": avg_rps
        })

    # ------------------------------------------------------
    # (New) Average Throughput per model
    #   Parse "throughput_tokens_per_second=XYZ" from result
    # ------------------------------------------------------
    logs_with_throughput = await sync_to_async(
        lambda: list(
            logs_with_model.filter(result__icontains="throughput_tokens_per_second")
            .values("model", "result")
        )
    )()

    # Parse values and group by model
    model_to_throughputs = {}
    for entry in logs_with_throughput:
        model_val = entry["model"]
        text_result = entry["result"] or ""

        # (updated) Use the improved regex to find the numeric value
        match = THROUGHPUT_PATTERN.search(text_result)
        if match:
            try:
                thpt = float(match.group(1))
                model_to_throughputs.setdefault(model_val, []).append(thpt)
            except ValueError:
                pass

    model_throughput = []
    for m, thpt_list in model_to_throughputs.items():
        avg_thpt = sum(thpt_list) / len(thpt_list)
        model_throughput.append({"model": m, "avg_throughput": avg_thpt})

    # ------------------------------------------------------
    # Return everything
    # ------------------------------------------------------
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
        "requests_per_user": requests_per_user,

        "weekly_usage_2_months": weekly_usage_2_months,
        "daily_usage_2_weeks": daily_usage_2_weeks,
        "average_rps_today": average_rps_today,

        # New fields
        "daily_rps_7_days": daily_rps_7_days,
        "model_throughput": model_throughput,
    }

@router.get("/metrics")
async def get_metrics(request):
    """Get dashboard metrics asynchronously."""
    try:
        log.info("Fetching metrics...")
        metrics = await fetch_metrics()
        return metrics
    except Exception as e:
        log.error(f"Error fetching metrics: {e}")
        return {"error": str(e)}, 500

@router.get("/endpoints")
async def get_endpoints(request):
    """Fetch active endpoints asynchronously."""
    try:
        endpoints = await sync_to_async(list)(Endpoint.objects.all())
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

class LogPaginationSchema(Schema):
    page: int
    per_page: int = 100

# Logs Endpoint
@router.get("/logs", response=List[LogResponseSchema])
async def get_logs(request, page: int = 0, per_page: int = 100):
    """Fetch logs asynchronously."""
    try:
        # Subqueries to annotate logs with model information
        subquery_model = Subquery(
            Endpoint.objects.filter(endpoint_slug=OuterRef("endpoint_slug")).values("model")[:1]
        )

        logs_query = await sync_to_async(
            lambda: list(
                Log.objects.annotate(
                    model=Coalesce(subquery_model, Value("")),
                    latency=F("timestamp_response") - F("timestamp_receive"),
                )
                .filter(response_status__isnull=False)
                .order_by("-timestamp_receive")
            )
        )()

        # Paginate logs
        start_index = page * per_page
        end_index = start_index + per_page
        paginated_logs = logs_query[start_index:end_index]

        # Map results to the schema
        logs = [
            LogResponseSchema(
                name=log.name,
                username=log.username,
                model=log.model,
                response_status=log.response_status,
                latency=log.latency.total_seconds() if log.latency else None,
            )
            for log in paginated_logs
        ]

        return logs
    except Exception as e:
        log.error(f"Error fetching paginated logs: {e}")
        return {"error": str(e)}, 500

# Analytics View (Render the analytics.html template)
@router.get("/analytics")
async def analytics_view(request):
    """Render the analytics dashboard."""
    return render(request, "analytics.html")

# Register Router with API
api.add_router("/", router)