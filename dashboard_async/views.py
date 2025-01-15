# dashboard_api.py (example filename)

from datetime import timedelta
from django.db.models import Count, Avg, F, Q, Subquery, OuterRef, Value, CharField
from django.db.models.functions import TruncWeek, TruncDay
from django.utils.timezone import now
from ninja import NinjaAPI, Router
from resource_server.models import Endpoint, Log
import re
import logging

log = logging.getLogger(__name__)

api = NinjaAPI(urls_namespace="dashboard_api")
router = Router()

# Regex for throughput_tokens_per_second in JSON-like or partial text
THROUGHPUT_PATTERN = re.compile(r'[\"\']throughput_tokens_per_second[\"\']\s*:\s*([\d\.]+)')

def fetch_metrics():
    """Fetch metrics synchronously."""

    # 1) Basic metrics
    total_requests = Log.objects.count()
    successful_requests = Log.objects.filter(Q(response_status=200) | Q(response_status__isnull=True)).count()
    failed_requests = total_requests - successful_requests
    total_users = Log.objects.values("username").distinct().count()
    user_details = list(Log.objects.values("name", "username").distinct())

    # 2) Annotate logs with model information
    endpoint_models = Endpoint.objects.filter(endpoint_slug=OuterRef("endpoint_slug")).values("model")[:1]
    logs_with_annotations = Log.objects.annotate(
        model=Subquery(endpoint_models, output_field=CharField()),
    )

    # Filter out logs with no/empty model
    logs_with_model = logs_with_annotations.filter(model__isnull=False).exclude(model="")

    # 3) Model-specific metrics
    model_requests = list(
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

    model_latency = list(
        logs_with_model.filter(Q(response_status=200) | Q(response_status__isnull=True))
        .values("model")
        .annotate(avg_latency=Avg(F("timestamp_response") - F("timestamp_receive")))
    )

    users_per_model = list(
        logs_with_model.values("model").annotate(
            user_count=Count("username", distinct=True)
        )
    )

    # 4) Requests per user
    requests_per_user = list(
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

    # 5) Weekly usage (past 2 months)
    two_months_ago = now() - timedelta(days=60)
    weekly_usage_2_months = list(
        Log.objects.filter(timestamp_receive__gte=two_months_ago)
        .annotate(week_start=TruncWeek("timestamp_receive"))
        .values("week_start")
        .annotate(request_count=Count("id"))
        .order_by("week_start")
    )

    # 6) Daily usage (past 2 weeks)
    two_weeks_ago = now() - timedelta(days=14)
    daily_usage_2_weeks = list(
        Log.objects.filter(timestamp_receive__gte=two_weeks_ago)
        .annotate(day=TruncDay("timestamp_receive"))
        .values("day")
        .annotate(request_count=Count("id"))
        .order_by("day")
    )

    # 7) Running average RPS for today
    day_start = now().replace(hour=0, minute=0, second=0, microsecond=0)
    requests_today = Log.objects.filter(timestamp_receive__gte=day_start).count()
    time_since_day_start = (now() - day_start).total_seconds()
    average_rps_today = requests_today / time_since_day_start if time_since_day_start > 0 else 0

    # 8) Daily average RPS (past 7 days)
    seven_days_ago = now() - timedelta(days=7)
    daily_counts_7_days = list(
        Log.objects.filter(timestamp_receive__gte=seven_days_ago)
        .annotate(day=TruncDay("timestamp_receive"))
        .values("day")
        .annotate(request_count=Count("id"))
        .order_by("day")
    )

    daily_rps_7_days = []
    for day_info in daily_counts_7_days:
        day_dt = day_info["day"]
        req_count = day_info["request_count"]

        if day_dt.date() == now().date():
            # partial day
            partial_seconds = (now() - day_start).total_seconds()
            avg_rps = req_count / partial_seconds if partial_seconds > 0 else 0
        else:
            # full day
            avg_rps = req_count / 86400.0

        daily_rps_7_days.append({
            "day": day_dt.isoformat(),
            "average_rps": avg_rps
        })

    # 9) Average throughput per model from "throughput_tokens_per_second"
    logs_with_throughput = list(
        logs_with_model.filter(result__icontains="throughput_tokens_per_second")
        .values("model", "result")
    )

    model_to_throughputs = {}
    for entry in logs_with_throughput:
        model_val = entry["model"]
        text_result = entry["result"] or ""
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


api.add_router("/", router)
