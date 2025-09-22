from datetime import timedelta
from django.db.models import Count, Avg, F, Q, Subquery, OuterRef, Value, CharField
from django.db.models.functions import TruncWeek, TruncDay
from django.utils.timezone import now
from django.utils import timezone
from django.utils.translation.trans_real import accept_language_re
from ninja import NinjaAPI, Router
from django.http import JsonResponse
from django.core.cache import cache
import json
from resource_server.models import Endpoint, Log, Batch
from resource_server_async.models import (
    User as AsyncUser,
    AccessLog as AsyncAccessLog,
    RequestLog as AsyncRequestLog,
    BatchLog as AsyncBatchLog,
)
import re
import logging

log = logging.getLogger(__name__)

api = NinjaAPI(urls_namespace="dashboard_api")
router = Router()

api.add_router("/", router)


# ========================= New Realtime Dashboard (Async tables, no MVs) =========================


@router.get("/analytics")
def analytics_realtime_view(request):
    from django.shortcuts import render
    return render(request, "realtime.html")


@router.get("/analytics/metrics")
def get_realtime_metrics(request):
    """Overall realtime metrics from RequestMetrics (no window)."""
    try:
        from django.db import connection

        # Choose source: union view if present, else base table
        access_log_table = "resource_server_async_accesslog_all"
        source_table = "resource_server_async_requestmetrics_all"
        with connection.cursor() as cursor:
            try:
                cursor.execute("SELECT 1 FROM resource_server_async_requestmetrics_all LIMIT 1")
            except Exception:
                source_table = "resource_server_async_requestmetrics"
        with connection.cursor() as cursor:
            try:
                cursor.execute("SELECT 1 FROM resource_server_async_accesslog_all LIMIT 1")
            except Exception:
                access_log_table = "resource_server_async_accesslog"

        # Overview aggregates
        # Query the access log table to get the total requests, successful requests are 0 and between 200 and 299, failed requests are >= 300
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT COUNT(*)::bigint AS total_requests,
                       COUNT(*) FILTER (WHERE status_code BETWEEN 200 AND 299 OR status_code = 0) AS successful,
                       COUNT(*) FILTER (WHERE status_code >= 300 OR status_code IS NULL) AS failed
                FROM {access_log_table}
                """
            )
            row = cursor.fetchone()
            total_requests, successful, failed = row
       
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT 
                  COALESCE(SUM(total_tokens), 0) AS total_tokens
                FROM {source_table}
                """
            )
            row = cursor.fetchone()
            total_tokens = row[0]

        success_rate = (successful / total_requests) if total_requests and total_requests > 0 else 0.0

        # Unique users: simply count from async User table (authorized users)
        try:
            unique_users = AsyncUser.objects.count()
        except Exception:
            # Fallback to 0 on any ORM error
            unique_users = 0

        # Per-model aggregates using RequestLog joined to AccessLog for status, and left-joining RequestMetrics for tokens
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT rl.model,
                       COUNT(*)::bigint AS total_requests,
                       COUNT(*) FILTER (WHERE al.status_code = 0 OR al.status_code BETWEEN 200 AND 299) AS successful,
                       COUNT(*) FILTER (WHERE al.status_code >= 300 OR al.status_code IS NULL) AS failed,
                       COALESCE(SUM(rm.total_tokens), 0) AS total_tokens
                FROM resource_server_async_requestlog rl
                JOIN resource_server_async_accesslog al ON al.id = rl.access_log_id
                LEFT JOIN resource_server_async_requestmetrics rm ON rm.request_id = rl.id
                GROUP BY rl.model
                ORDER BY total_requests DESC
                """
            )
            per_model = [
                {
                    "model": row[0],
                    "total_requests": int(row[1] or 0),
                    "successful": int(row[2] or 0),
                    "failed": int(row[3] or 0),
                    "total_tokens": int(row[4] or 0),
                }
                for row in cursor.fetchall()
            ]

        return {
            "totals": {
                "total_tokens": int(total_tokens or 0),
                "total_requests": int(total_requests or 0),
                "successful": int(successful or 0),
                "failed": int(failed or 0),
                "success_rate": success_rate,
                "unique_users": int(unique_users or 0),
            },
            "per_model": per_model,
            "time_bounds": None,
        }
    except Exception as e:
        log.error(f"Error fetching realtime metrics: {e}")
        return JsonResponse({"error": str(e)}, status=500)


@router.get("/analytics/logs")
def get_realtime_logs(request, page: int = 0, per_page: int = 500):
    """Latest AccessLog with optional joined RequestLog and User (LEFT JOIN semantics)."""
    try:
        start_index = page * per_page
        end_index = start_index + per_page

        # LEFT JOIN semantics: start from AccessLog, bring in request_log and user when present
        # Utilize DB indexes: order by indexed timestamp_request desc, then status_code
        qs = (
            AsyncAccessLog.objects
            .select_related("user", "request_log")
            .order_by("-timestamp_request", "-status_code")
        )

        sliced = qs[start_index:end_index]

        results = []
        for al in sliced:
            rl = getattr(al, "request_log", None)
            user = getattr(al, "user", None)

            # Compute latency secs from request_log if available
            latency_secs = None
            if rl and rl.timestamp_compute_response and rl.timestamp_compute_request:
                latency_secs = (rl.timestamp_compute_response - rl.timestamp_compute_request).total_seconds()

            # Truncated prompt and result from request_log when available
            def _truncate(val: str, limit: int = 500) -> str:
                if not val:
                    return ""
                try:
                    text = str(val)
                except Exception:
                    text = ""
                if len(text) > limit:
                    return text[:limit] + "…"
                return text

            results.append({
                "request_id": str(rl.id) if rl else None,
                "cluster": rl.cluster if rl else None,
                "model": rl.model if rl else None,
                "openai_endpoint": rl.openai_endpoint if rl else None,
                "timestamp_request": al.timestamp_request.isoformat() if al and al.timestamp_request else None,
                "latency_seconds": latency_secs,
                "task_uuid": rl.task_uuid if rl else None,
                "accesslog_id": str(al.id),
                "status_code": al.status_code,
                "error_message": al.error,
                "error_snippet": _truncate(al.error) if al and al.error else "",
                "api_route": al.api_route,
                "prompt_snippet": _truncate(rl.prompt) if rl else "",
                "result_snippet": _truncate(rl.result) if rl else "",
                "user_id": str(user.id) if user else None,
                "user_name": user.name if user else None,
                "user_username": user.username if user else None,
                "idp_id": user.idp_id if user else None,
                "idp_name": user.idp_name if user else None,
                "auth_service": user.auth_service if user else None,
            })

        return results
    except Exception as e:
        log.error(f"Error fetching realtime logs: {e}")
        return JsonResponse({"error": str(e)}, status=500)


# ===== Additional realtime endpoints from RequestMetrics =====

def _parse_series_window(window: str):
    """Map UI window to a time delta and Postgres date_trunc unit.
    Supported: 1h->minute, 1d->hour, 1w->day, 1m->week, 1y->month.
    """
    window = (window or "1d").strip().lower()
    if window == "1h":
        return timedelta(hours=1), "minute"
    if window in ("1d", "24h"):
        return timedelta(days=1), "hour"
    if window in ("1w", "7d"):
        return timedelta(days=7), "day"
    if window in ("1m", "30d"):
        return timedelta(days=30), "week"
    if window in ("1y", "12m"):
        return timedelta(days=365), "month"
    if window in ("3y", "36m"):
        return timedelta(days=365*3), "month"
    # default
    return timedelta(days=1), "hour"


@router.get("/analytics/users-per-model")
def get_users_per_model(request):
    try:
        from django.db import connection
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT rl.model, COUNT(DISTINCT al.user_id) AS user_count
                FROM resource_server_async_requestlog rl
                JOIN resource_server_async_accesslog al ON al.id = rl.access_log_id
                WHERE al.user_id IS NOT NULL AND al.user_id <> ''
                GROUP BY rl.model
                ORDER BY user_count DESC
                """
            )
            rows = cursor.fetchall()
        return [{"model": r[0], "user_count": int(r[1] or 0)} for r in rows]
    except Exception as e:
        log.error(f"Error fetching users per model: {e}")
        return JsonResponse({"error": str(e)}, status=500)


@router.get("/analytics/users-table")
def get_users_table(request):
    """Tabular list of users with last access, success/failure counts, success%, last failure time."""
    try:
        from django.db import connection
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT 
                  u.name,
                  u.username,
                  MAX(al.timestamp_request) AS last_access,
                  COUNT(*) FILTER (WHERE al.status_code = 0 OR al.status_code BETWEEN 200 AND 299) AS successful,
                  COUNT(*) FILTER (WHERE al.status_code >= 300 OR al.status_code IS NULL) AS failed,
                  MAX(al.timestamp_request) FILTER (WHERE al.status_code >= 300 OR al.status_code IS NULL) AS last_failure
                FROM resource_server_async_user u
                LEFT JOIN resource_server_async_accesslog al ON al.user_id = u.id
                GROUP BY u.name, u.username
                ORDER BY last_access DESC NULLS LAST, u.username
                """
            )
            rows = cursor.fetchall()

        results = []
        for r in rows:
            name, username, last_access, successful, failed, last_failure = r
            total = int((successful or 0)) + int((failed or 0))
            success_rate = (float(successful) / total) if total > 0 else 0.0
            results.append({
                "name": name,
                "username": username,
                "last_access": last_access.isoformat() if last_access else None,
                "successful": int(successful or 0),
                "failed": int(failed or 0),
                "success_rate": success_rate,
                "last_failure": last_failure.isoformat() if last_failure else None,
            })
        return results
    except Exception as e:
        log.error(f"Error fetching users table: {e}")
        return JsonResponse({"error": str(e)}, status=500)


@router.get("/analytics/series")
def get_overall_series(request, window: str = "24h"):
    try:
        from django.db import connection
        delta, trunc_unit = _parse_series_window(window)
        end_ts = timezone.now()
        start_ts = end_ts - delta
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                WITH series AS (
                  SELECT generate_series(
                    date_trunc(%s, %s::timestamptz),
                    date_trunc(%s, %s::timestamptz),
                    CASE %s
                      WHEN 'minute' THEN interval '1 minute'
                      WHEN 'hour' THEN interval '1 hour'
                      WHEN 'day' THEN interval '1 day'
                      WHEN 'week' THEN interval '1 week'
                      WHEN 'month' THEN interval '1 month'
                    END
                  ) AS bucket
                )
                SELECT s.bucket,
                       COALESCE(a.ok, 0) AS ok,
                       COALESCE(a.fail, 0) AS fail
                FROM series s
                LEFT JOIN (
                  SELECT date_trunc(%s, timestamp_request) AS bucket,
                         COUNT(*) FILTER (WHERE status_code=0 OR status_code BETWEEN 200 AND 299) AS ok,
                         COUNT(*) FILTER (WHERE status_code >= 300 OR status_code IS NULL) AS fail
                  FROM resource_server_async_accesslog
                  WHERE timestamp_request >= %s AND timestamp_request <= %s
                  GROUP BY bucket
                ) a ON a.bucket = s.bucket
                ORDER BY s.bucket
                """,
                [trunc_unit, start_ts, trunc_unit, end_ts, trunc_unit, trunc_unit, start_ts, end_ts]
            )
            rows = cursor.fetchall()
        total_ok = sum(int(r[1] or 0) for r in rows)
        total_fail = sum(int(r[2] or 0) for r in rows)
        # Extra debug: raw counts in time-range
        try:
            from django.db import connection as _conn
            with _conn.cursor() as c:
                c.execute(
                    """
                    SELECT 
                      COUNT(*) FILTER (WHERE status_code=0 OR status_code BETWEEN 200 AND 299) AS ok,
                      COUNT(*) FILTER (WHERE status_code >= 300 OR status_code IS NULL) AS fail
                    FROM resource_server_async_accesslog
                    WHERE timestamp_request >= %s AND timestamp_request <= %s
                    """,
                    [start_ts, end_ts]
                )
                ok_range, fail_range = c.fetchone()
        except Exception:
            log.info(f"overall_series: points={len(rows)} total_ok={total_ok} total_fail={total_fail}")
        return [{"t": r[0].isoformat(), "ok": int(r[1] or 0), "fail": int(r[2] or 0)} for r in rows]
    except Exception as e:
        log.error(f"Error fetching overall series: {e}")
        return JsonResponse({"error": str(e)}, status=500)


@router.get("/analytics/model/series")
def get_model_series(request, model: str, window: str = "24h"):
    try:
        from django.db import connection
        delta, trunc_unit = _parse_series_window(window)
        end_ts = timezone.now()
        start_ts = end_ts - delta
        log.debug(f"model_series: model={model} window={window} trunc={trunc_unit} start={start_ts.isoformat()} end={end_ts.isoformat()}")
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                WITH series AS (
                  SELECT generate_series(
                    date_trunc(%s, %s::timestamptz),
                    date_trunc(%s, %s::timestamptz),
                    CASE %s
                      WHEN 'minute' THEN interval '1 minute'
                      WHEN 'hour' THEN interval '1 hour'
                      WHEN 'day' THEN interval '1 day'
                      WHEN 'week' THEN interval '1 week'
                      WHEN 'month' THEN interval '1 month'
                    END
                  ) AS bucket
                )
                SELECT s.bucket,
                       COALESCE(a.ok, 0) AS ok,
                       COALESCE(a.fail, 0) AS fail
                FROM series s
                LEFT JOIN (
                  SELECT date_trunc(%s, al.timestamp_request) AS bucket,
                         COUNT(*) FILTER (WHERE al.status_code=0 OR al.status_code BETWEEN 200 AND 299) AS ok,
                         COUNT(*) FILTER (WHERE al.status_code >= 300 OR al.status_code IS NULL) AS fail
                  FROM resource_server_async_accesslog al JOIN resource_server_async_requestlog rl ON al.id = rl.access_log_id
                  WHERE rl.model = %s AND al.timestamp_request >= %s AND al.timestamp_request <= %s
                  GROUP BY bucket
                ) a ON a.bucket = s.bucket
                ORDER BY s.bucket
                """,
                [trunc_unit, start_ts, trunc_unit, end_ts, trunc_unit, trunc_unit, model, start_ts, end_ts]
            )
            rows = cursor.fetchall()
        total_ok = sum(int(r[1] or 0) for r in rows)
        total_fail = sum(int(r[2] or 0) for r in rows)
        log.debug(f"model_series: model={model} points={len(rows)} total_ok={total_ok} total_fail={total_fail}")
        return [{"t": r[0].isoformat(), "ok": int(r[1] or 0), "fail": int(r[2] or 0)} for r in rows]
    except Exception as e:
        log.error(f"Error fetching model series: {e}")
        return JsonResponse({"error": str(e)}, status=500)


@router.get("/analytics/model/box")
def get_model_box(request, model: str, window: str = "24h"):
    try:
        from django.db import connection
        delta, _ = _parse_series_window(window)
        end_ts = timezone.now()
        start_ts = end_ts - delta
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                  AVG(throughput_tokens_per_sec),
                  PERCENTILE_DISC(0.5) WITHIN GROUP (ORDER BY throughput_tokens_per_sec),
                  PERCENTILE_DISC(0.99) WITHIN GROUP (ORDER BY throughput_tokens_per_sec),
                  AVG(response_time_sec),
                  PERCENTILE_DISC(0.5) WITHIN GROUP (ORDER BY response_time_sec),
                  PERCENTILE_DISC(0.99) WITHIN GROUP (ORDER BY response_time_sec)
                FROM resource_server_async_requestmetrics
                WHERE model = %s AND timestamp_compute_request >= %s AND timestamp_compute_request <= %s
                  AND throughput_tokens_per_sec IS NOT NULL AND response_time_sec IS NOT NULL
                """,
                [model, start_ts, end_ts]
            )
            row = cursor.fetchone()
        return {
            "throughput": {"mean": float(row[0] or 0.0), "p50": float(row[1] or 0.0), "p99": float(row[2] or 0.0)},
            "latency": {"mean": float(row[3] or 0.0), "p50": float(row[4] or 0.0), "p99": float(row[5] or 0.0)}
        }
    except Exception as e:
        log.error(f"Error fetching model box: {e}")
        return JsonResponse({"error": str(e)}, status=500)


@router.get("/analytics/health")
def get_health_status(request, cluster: str = "sophia", refresh: int = 0):
    """Proxy health info so the browser doesn't need a bearer token.
    Combines qstat job data with configured endpoints to mark offline models.
    """
    try:
        from asgiref.sync import async_to_sync
        from resource_server_async.utils import get_qstat_details

        # Try cache first unless refresh requested
        cache_key = f"dashboard_health:{cluster}"
        if not refresh:
            cached_payload = cache.get(cache_key)
            if cached_payload:
                return JsonResponse(cached_payload)

        # Fetch qstat details via internal async util
        qres, _, err, code = async_to_sync(get_qstat_details)(cluster)
        # Normalize qres (may arrive as JSON string)
        def to_dict(raw):
            if isinstance(raw, dict):
                return raw
            if isinstance(raw, str):
                try:
                    data = json.loads(raw)
                    if isinstance(data, str):
                        try:
                            data2 = json.loads(data)
                            if isinstance(data2, dict):
                                return data2
                        except Exception:
                            return {}
                    if isinstance(data, dict):
                        return data
                except Exception:
                    return {}
            return {}
        q = to_dict(qres)
        try:
            sample_running = (q or {}).get("running", [])[:1]
        except Exception as e:
            log.error(f"Health status sample running: {e}")
            pass
        if err:
            # Serve stale cache if available
            cached_payload = cache.get(cache_key)
            if cached_payload:
                return JsonResponse(cached_payload)
            # Fallback: mark configured endpoints as offline
            configured_models = set(Endpoint.objects.filter(cluster=cluster).values_list("model", flat=True))
            items = [{
                "model": m,
                "status": "offline",
                "nodes_reserved": "",
                "host_name": "",
                "start_info": "",
            } for m in sorted(configured_models)]
            payload = {"items": items, "free_nodes": None}
            cache.set(cache_key, payload, timeout=60)
            return JsonResponse(payload)

        running = q.get("running", []) or []
        queued = q.get("queued", []) or []

        def process(entries, status):
            rows = []
            for e in entries:
                if not isinstance(e, dict):
                    continue
                models_str = str(e.get("Models", ""))
                models = [m.strip() for m in models_str.split(",") if m.strip()]
                for m in models:
                    rows.append({
                        "model": m,
                        "status": status,
                        "nodes_reserved": e.get("Nodes Reserved"),
                        "host_name": e.get("Host Name"),
                        "start_info": e.get("Job Comments"),
                    })
            return rows

        items = process(running, "running") + process(queued, "queued")
        present_models = {i["model"] for i in items}

        # Gather configured endpoint models for the cluster
        configured_models = set(Endpoint.objects.filter(cluster=cluster).values_list("model", flat=True))
        for m in sorted(configured_models - present_models):
            items.append({
                "model": m,
                "status": "offline",
                "nodes_reserved": "",
                "host_name": "",
                "start_info": "",
            })

        payload = {
            "items": items,
            "free_nodes": (q.get("cluster_status", {}) or {}).get("free_nodes")
        }
        # Update cache if changed
        cached_payload = cache.get(cache_key)
        if cached_payload != payload:
            cache.set(cache_key, payload, timeout=120)
        return JsonResponse(payload)
    except Exception as e:
        log.error(f"Error fetching health status: {e}")
        return JsonResponse({"error": str(e)}, status=500)


# ========= Additional realtime endpoints =========

@router.get("/analytics/requests-per-user")
def get_requests_per_user(request):
    """Overall requests per user (from RequestMetrics joined to AccessLog/User)."""
    try:
        from django.db import connection
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT u.name, u.username,
                       COUNT(*)::bigint AS total,
                       COUNT(*) FILTER (WHERE al.status_code=0 OR al.status_code BETWEEN 200 AND 299) AS successful,
                       COUNT(*) FILTER (WHERE al.status_code >= 300 OR al.status_code IS NULL) AS failed
                FROM resource_server_async_accesslog al
                JOIN resource_server_async_user u ON u.id = al.user_id
                GROUP BY u.name, u.username
                ORDER BY total DESC
                """
            )
            rows = cursor.fetchall()
        return [
            {"name": r[0], "username": r[1], "total": int(r[2] or 0), "successful": int(r[3] or 0), "failed": int(r[4] or 0)}
            for r in rows
        ]
    except Exception as e:
        log.error(f"Error fetching requests per user: {e}")
        return JsonResponse({"error": str(e)}, status=500)


@router.get("/analytics/batch/overview")
def get_batch_overview(request):
    """Batch metrics overview (prefers BatchMetrics, falls back to parsing BatchLog.result)."""
    try:
        from django.db import connection
        # Try BatchMetrics
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT COALESCE(SUM(total_tokens),0) AS tokens,
                           COALESCE(SUM(num_responses),0) AS requests,
                           COUNT(*)::bigint AS total_jobs,
                           COUNT(*) FILTER (WHERE status = 'completed') AS completed_jobs
                    FROM resource_server_async_batchmetrics
                    """
                )
                row = cursor.fetchone()
                if row is not None and any(row):
                    total_tokens = int(row[0] or 0)
                    total_requests = int(row[1] or 0)
                    total_jobs = int(row[2] or 0)
                    completed_jobs = int(row[3] or 0)
                    success_rate = (completed_jobs / total_jobs) if total_jobs > 0 else 0.0
                    return {
                        "total_tokens": total_tokens,
                        "total_requests": total_requests,
                        "total_jobs": total_jobs,
                        "completed_jobs": completed_jobs,
                        "success_rate": success_rate,
                    }
        except Exception:
            pass

        # Fallback to BatchLog parsing
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT 
                  COALESCE(SUM((CASE WHEN jsonb_typeof(result::jsonb -> 'metrics') = 'object' 
                                     THEN (result::jsonb -> 'metrics' ->> 'total_tokens')::bigint ELSE 0 END)),0) AS tokens,
                  COALESCE(SUM((CASE WHEN jsonb_typeof(result::jsonb -> 'metrics') = 'object' 
                                     THEN (result::jsonb -> 'metrics' ->> 'num_responses')::bigint ELSE 0 END)),0) AS requests,
                  COUNT(*)::bigint AS total_jobs,
                  COUNT(*) FILTER (WHERE status = 'completed') AS completed_jobs
                FROM resource_server_async_batchlog
                """
            )
            row = cursor.fetchone()
        total_tokens = int(row[0] or 0)
        total_requests = int(row[1] or 0)
        total_jobs = int(row[2] or 0)
        completed_jobs = int(row[3] or 0)
        success_rate = (completed_jobs / total_jobs) if total_jobs > 0 else 0.0
        return {
            "total_tokens": total_tokens,
            "total_requests": total_requests,
            "total_jobs": total_jobs,
            "completed_jobs": completed_jobs,
            "success_rate": success_rate,
        }
    except Exception as e:
        log.error(f"Error fetching batch overview: {e}")
        return JsonResponse({"error": str(e)}, status=500)


@router.get("/analytics/batch/model-summary")
def get_batch_model_summary(request, model: str):
    """Batch model throughput/latency summary (mean, p50, p99)."""
    try:
        from django.db import connection
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT 
                  AVG(throughput_tokens_per_sec),
                  PERCENTILE_DISC(0.5) WITHIN GROUP (ORDER BY throughput_tokens_per_sec),
                  PERCENTILE_DISC(0.99) WITHIN GROUP (ORDER BY throughput_tokens_per_sec),
                  AVG(response_time_sec),
                  PERCENTILE_DISC(0.5) WITHIN GROUP (ORDER BY response_time_sec),
                  PERCENTILE_DISC(0.99) WITHIN GROUP (ORDER BY response_time_sec)
                FROM resource_server_async_batchmetrics
                WHERE model = %s
                  AND throughput_tokens_per_sec IS NOT NULL AND response_time_sec IS NOT NULL
                """,
                [model]
            )
            row = cursor.fetchone()
        return {
            "throughput": {"mean": float(row[0] or 0.0), "p50": float(row[1] or 0.0), "p99": float(row[2] or 0.0)},
            "latency": {"mean": float(row[3] or 0.0), "p50": float(row[4] or 0.0), "p99": float(row[5] or 0.0)}
        }
    except Exception as e:
        log.error(f"Error fetching batch model summary: {e}")
        return JsonResponse({"error": str(e)}, status=500)


@router.get("/analytics/batch-logs")
def get_batch_logs_rt(request, page: int = 0, per_page: int = 100):
    """Paginated batch logs from Async tables with user info and duration."""
    try:
        start_index = page * per_page
        end_index = start_index + per_page
        qs = (AsyncBatchLog.objects
              .select_related("access_log__user")
              .order_by("-completed_at", "-in_progress_at"))
        sliced = qs[start_index:end_index]
        results = []
        for bl in sliced:
            access = getattr(bl, "access_log", None)
            user = getattr(access, "user", None) if access else None
            duration = None
            if bl.completed_at and bl.in_progress_at:
                duration = (bl.completed_at - bl.in_progress_at).total_seconds()
            results.append({
                "time": (bl.completed_at or bl.in_progress_at).isoformat() if (bl.completed_at or bl.in_progress_at) else None,
                "name": user.name if user else None,
                "username": user.username if user else None,
                "model": bl.model,
                "cluster": bl.cluster,
                "status": bl.status,
                "latency": duration,
            })
        return results
    except Exception as e:
        log.error(f"Error fetching batch logs rt: {e}")
        return JsonResponse({"error": str(e)}, status=500)
