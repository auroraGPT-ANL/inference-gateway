from datetime import timedelta
from django.db.models import Count, Avg, F, Q, Subquery, OuterRef, Value, CharField
from django.db.models.functions import TruncWeek, TruncDay
from django.utils.timezone import now
from django.utils import timezone
from django.utils.translation.trans_real import accept_language_re
from ninja import NinjaAPI, Router
from ninja.security import SessionAuth
from django.http import JsonResponse, HttpRequest
from django.core.cache import cache
from django.contrib.auth.decorators import login_required
from django.contrib.auth import views as auth_views
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm
from django.shortcuts import render, redirect
from django.contrib import messages
from django.urls import reverse_lazy
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
import utils.metis_utils as metis_utils

log = logging.getLogger(__name__)


# Custom authentication for Django Ninja that uses Globus session authentication
class DjangoSessionAuth(SessionAuth):
    """Use Globus session authentication for API endpoints."""
    
    def authenticate(self, request: HttpRequest, key):
        from dashboard_async.globus_auth import validate_dashboard_token
        import time
        
        # Check for Globus tokens in session
        if 'globus_tokens' not in request.session:
            return None
        
        try:
            tokens = request.session['globus_tokens']
            auth_tokens = tokens.get('auth.globus.org', {})
            access_token = auth_tokens.get('access_token')
            expires_at = auth_tokens.get('expires_at_seconds', 0)
            
            # Check if token is still valid (not expired)
            if time.time() >= expires_at:
                return None
            
            # Validate token with groups check
            groups_token = tokens.get('groups.api.globus.org', {}).get('access_token')
            is_valid, user_data, error = validate_dashboard_token(access_token, groups_token)
            
            if is_valid:
                # Return user data for use in API views
                return user_data
            
        except Exception as e:
            log.warning(f"API auth error: {e}")
        
        return None


# Create Ninja API with session authentication
api = NinjaAPI(urls_namespace="dashboard_api", auth=DjangoSessionAuth())
router = Router()

api.add_router("/", router)


# ========================= Authentication Views =========================

def dashboard_login_view(request):
    """Initiate Globus OAuth2 login flow."""
    from dashboard_async.globus_auth import validate_dashboard_token
    
    # Check if already authenticated via Globus
    if 'globus_tokens' in request.session:
        tokens = request.session['globus_tokens']
        access_token = tokens['auth.globus.org']['access_token']
        groups_token = tokens.get('groups.api.globus.org', {}).get('access_token')
        is_valid, user_data, error = validate_dashboard_token(access_token, groups_token)
        
        if is_valid:
            return redirect('dashboard_analytics')
        else:
            # Clear invalid session to prevent loops
            request.session.flush()
    
    # Clear any stale OAuth state from previous failed attempts
    # This prevents reusing OAuth state after errors
    request.session.pop('oauth_state', None)
    request.session.pop('next_url', None)
    
    # Generate new state for CSRF protection
    import secrets
    state = secrets.token_urlsafe(32)
    request.session['oauth_state'] = state
    request.session['next_url'] = request.GET.get('next', 'dashboard_analytics')
    
    # Force session save before redirect
    request.session.modified = True
    request.session.save()
    
    # Get Globus authorization URL
    from dashboard_async.globus_auth import get_authorization_url
    auth_url = get_authorization_url(state=state)
    
    return redirect(auth_url)


def dashboard_callback_view(request):
    """Handle Globus OAuth2 callback."""
    from dashboard_async.globus_auth import exchange_code_for_tokens, validate_dashboard_token
    
    # Check for errors from Globus
    error = request.GET.get('error')
    if error:
        error_description = request.GET.get('error_description', error)
        log.error(f"Globus OAuth error: {error} - {error_description}")
        
        request.session.pop('oauth_state', None)
        request.session.pop('next_url', None)
        
        if error == 'unauthorized_client':
            messages.error(request, 
                f'OAuth Configuration Error: The redirect URI is not registered with Globus. '
                f'Error: {error_description}')
        else:
            messages.error(request, f'Authentication failed: {error_description}')
        
        return render(request, 'login.html', {'form': None})
    
    # Verify CSRF state
    state = request.GET.get('state')
    saved_state = request.session.get('oauth_state')
    
    if not state or state != saved_state:
        log.warning(f"CSRF state mismatch")
        messages.error(request, 'Invalid authentication state. Please try again.')
        request.session.pop('oauth_state', None)
        return render(request, 'login.html', {'form': None})
    
    # Exchange authorization code for tokens
    auth_code = request.GET.get('code')
    if not auth_code:
        log.error("No authorization code in callback")
        messages.error(request, 'No authorization code received from Globus.')
        return render(request, 'login.html', {'form': None})
    
    try:
        # Get tokens from Globus
        tokens = exchange_code_for_tokens(auth_code)
        request.session['globus_tokens'] = tokens
        
        # Validate token and get user info
        access_token = tokens['auth.globus.org']['access_token']
        groups_token = tokens.get('groups.api.globus.org', {}).get('access_token')
        is_valid, user_data, error = validate_dashboard_token(access_token, groups_token)
        
        if not is_valid:
            log.error(f"Token validation failed: {error}")
            
            # Clear all OAuth-related session data
            request.session.pop('globus_tokens', None)
            request.session.pop('oauth_state', None)
            request.session.pop('next_url', None)
            
            # Render error page directly (don't redirect to avoid loop)
            messages.error(request, error)
            return render(request, 'login.html', {'form': None})
        
        # Store user info in session
        request.session['globus_user'] = {
            'id': user_data.id,
            'name': user_data.name,
            'username': user_data.username,
            'idp_id': user_data.idp_id,
            'idp_name': user_data.idp_name,
        }
        
        request.session.modified = True
        request.session.save()
        
        log.info(f"Dashboard login successful: {user_data.name} ({user_data.username})")
        
        # Redirect to original destination
        next_url = request.session.pop('next_url', 'dashboard_analytics')
        request.session.pop('oauth_state', None)
        
        return redirect(next_url)
        
    except Exception as e:
        log.error(f"OAuth callback error: {e}")
        messages.error(request, f'Authentication error: {str(e)}')
        return redirect('dashboard_login')


def dashboard_logout_view(request):
    """Logout and clear both local and Globus sessions."""
    from dashboard_async.globus_auth import revoke_token
    from django.conf import settings
    from urllib.parse import urlencode
    
    # Revoke Globus tokens if present
    if 'globus_tokens' in request.session:
        try:
            access_token = request.session['globus_tokens']['auth.globus.org']['access_token']
            revoke_token(access_token)
        except Exception as e:
            log.warning(f"Token revocation error during logout: {e}")
    
    # Clear local session
    request.session.flush()
    
    # Build Globus logout URL with redirect back to login
    # This ensures the Globus session is also cleared
    logout_redirect = request.build_absolute_uri('/dashboard/login')
    globus_logout_url = f"https://auth.globus.org/v2/web/logout?{urlencode({'redirect_uri': logout_redirect})}"
    
    log.info("Logging out and clearing Globus session")
    
    # Redirect to Globus logout, which will then redirect back to our login
    return redirect(globus_logout_url)


# Password change views removed - Globus manages authentication


# ========================= Globus Authentication Decorator =========================

def globus_login_required(view_func):
    """
    Decorator to require Globus authentication for dashboard views.
    Validates Globus token from session and refreshes if needed.
    """
    from functools import wraps
    from dashboard_async.globus_auth import validate_dashboard_token, refresh_access_token
    import time
    
    @wraps(view_func)
    def wrapped_view(request, *args, **kwargs):
        # Check if Globus tokens exist in session
        if 'globus_tokens' not in request.session:
            messages.warning(request, 'Please log in to access the dashboard.')
            return redirect(f"/dashboard/login?next={request.path}")
        
        try:
            tokens = request.session['globus_tokens']
            auth_tokens = tokens.get('auth.globus.org', {})
            access_token = auth_tokens.get('access_token')
            expires_at = auth_tokens.get('expires_at_seconds', 0)
            
            # Check if token is expired or about to expire (within 5 minutes)
            if time.time() >= (expires_at - 300):
                # Try to refresh token
                refresh_token = auth_tokens.get('refresh_token')
                if refresh_token:
                    try:
                        new_tokens = refresh_access_token(refresh_token)
                        # Update session with new tokens
                        request.session['globus_tokens']['auth.globus.org'].update(new_tokens)
                        access_token = new_tokens['access_token']
                    except Exception as e:
                        log.warning(f"Token refresh failed: {e}")
                        messages.error(request, 'Your session has expired. Please log in again.')
                        return redirect('dashboard_login')
                else:
                    messages.error(request, 'Your session has expired. Please log in again.')
                    return redirect('dashboard_login')
            
            # Validate token
            groups_token = tokens.get('groups.api.globus.org', {}).get('access_token')
            is_valid, user_data, error = validate_dashboard_token(access_token, groups_token)
            
            if not is_valid:
                log.warning(f"Token validation failed: {error}")
                messages.error(request, f'Authentication failed: {error}')
                # Clear invalid session
                request.session.flush()
                return redirect('dashboard_login')
            
            # Store user info in request for use in view
            request.globus_user = user_data
            
            return view_func(request, *args, **kwargs)
            
        except Exception as e:
            log.error(f"Authentication error: {e}")
            messages.error(request, 'Authentication error. Please log in again.')
            request.session.flush()
            return redirect('dashboard_login')
    
    return wrapped_view


# ========================= New Realtime Dashboard (Async tables, no MVs) =========================


@globus_login_required
def analytics_realtime_view(request):
    """Main dashboard view - regular Django view (not API endpoint)."""
    # Access Globus user info via request.globus_user
    context = {
        'user': request.globus_user
    }
    return render(request, "realtime.html", context)

@router.get("/analytics/metrics")
def get_realtime_metrics(request, cluster: str = "all"):
    """Overall realtime metrics from RequestMetrics (no window)."""
    try:
        # Check cache first (include cluster in cache key)
        cache_key = f"dashboard:realtime_metrics:{cluster}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached
        
        from django.db import connection
        
        # Build cluster filter condition
        cluster_filter = ""
        cluster_params = []
        if cluster and cluster.lower() != "all":
            cluster_filter = "AND rl.cluster = %s"
            cluster_params = [cluster.lower()]

        # Refined breakdown of requests using HTTP status codes:
        # - Successful: status_code 200-299 or 0 (all successful requests)
        # - Failed Auth: status_code = 401 OR 403 OR (status_code >= 300 AND no RequestLog)
        #   - 401 = Unauthorized (authentication failed)
        #   - 403 = Forbidden (authorization failed)
        #   - No RequestLog with error status = failed before reaching inference (likely auth/validation)
        # - Failed Inference: Has RequestLog AND status_code >= 300 AND status_code NOT IN (401, 403)
        #   - These reached inference but failed during processing
        #
        # OPTIMIZED: Use single JOIN-based query instead of correlated subqueries for better performance
        with connection.cursor() as cursor:
            if cluster and cluster.lower() != "all":
                # Cluster-filtered query using INNER JOIN - only count requests that reached this cluster
                cursor.execute(
                    """
                    SELECT 
                        COUNT(*)::bigint AS total_all_requests,
                        COUNT(*) FILTER (WHERE al.status_code = 0 OR al.status_code BETWEEN 200 AND 299) AS successful_requests,
                        COUNT(*) FILTER (WHERE al.status_code IN (401, 403)) AS auth_failures,
                        COUNT(*) FILTER (WHERE al.status_code >= 300 AND al.status_code NOT IN (401, 403)) AS failed_inference,
                        (SELECT COALESCE(SUM(total_tokens), 0) FROM resource_server_async_requestmetrics rm WHERE rm.cluster = %s) AS total_tokens
                    FROM resource_server_async_accesslog al
                    INNER JOIN resource_server_async_requestlog rl 
                        ON rl.access_log_id = al.id AND rl.cluster = %s
                    """,
                    [cluster.lower(), cluster.lower()]
                )
            else:
                # Non-filtered query using LEFT JOIN - much faster than correlated subqueries
                cursor.execute(
                    """
                    WITH access_with_request AS (
                        SELECT 
                            al.id,
                            al.status_code,
                            CASE WHEN rl.id IS NOT NULL THEN 1 ELSE 0 END AS has_request_log
                        FROM resource_server_async_accesslog al
                        LEFT JOIN resource_server_async_requestlog rl 
                            ON rl.access_log_id = al.id
                    )
                    SELECT 
                        COUNT(*)::bigint AS total_all_requests,
                        COUNT(*) FILTER (WHERE status_code = 0 OR status_code BETWEEN 200 AND 299) AS successful_requests,
                        COUNT(*) FILTER (WHERE (
                            status_code IN (401, 403)
                            OR (has_request_log = 0 AND status_code >= 300)
                        )) AS auth_failures,
                        COUNT(*) FILTER (WHERE has_request_log = 1 AND status_code >= 300 AND status_code NOT IN (401, 403)) AS failed_inference,
                        (SELECT COALESCE(SUM(total_tokens), 0) FROM resource_server_async_requestmetrics) AS total_tokens
                    FROM access_with_request
                    """
                )
            row = cursor.fetchone()
            total_all_requests, successful_requests, auth_failures, failed_inference, total_tokens = row
        
        # Success rate calculation:
        # - Numerator: Successful requests (AccessLog with 200-299)
        # - Denominator: All requests that reached inference (successful + failed_inference)
        # - Excludes: Auth failures (never reached inference)
        total_inference_requests = successful_requests + failed_inference
        
        # Success rate based on real request/response (not auth failures)
        success_rate = (successful_requests / total_inference_requests) if total_inference_requests and total_inference_requests > 0 else 0.0
        
        # Unique users: count users who have requests in this cluster
        try:
            if cluster and cluster.lower() != "all":
                unique_users = AsyncUser.objects.filter(
                    access_logs__request_log__cluster=cluster.lower()
                ).distinct().count()
            else:
                unique_users = AsyncUser.objects.count()
        except Exception:
            # Fallback to 0 on any ORM error
            unique_users = 0

        # OPTIMIZED: Per-model aggregates directly from RequestMetrics (with cluster filter if needed)
        with connection.cursor() as cursor:
            if cluster and cluster.lower() != "all":
                cursor.execute(
                    """
                    SELECT model,
                           COUNT(*)::bigint AS total_requests,
                           COUNT(*) FILTER (WHERE status_code = 0 OR status_code BETWEEN 200 AND 299) AS successful,
                           COUNT(*) FILTER (WHERE status_code >= 300 OR status_code IS NULL) AS failed,
                           COALESCE(SUM(total_tokens), 0) AS total_tokens
                    FROM resource_server_async_requestmetrics
                    WHERE cluster = %s
                    GROUP BY model
                    ORDER BY total_requests DESC
                    """,
                    [cluster.lower()]
                )
            else:
                cursor.execute(
                    """
                    SELECT model,
                           COUNT(*)::bigint AS total_requests,
                           COUNT(*) FILTER (WHERE status_code = 0 OR status_code BETWEEN 200 AND 299) AS successful,
                           COUNT(*) FILTER (WHERE status_code >= 300 OR status_code IS NULL) AS failed,
                           COALESCE(SUM(total_tokens), 0) AS total_tokens
                    FROM resource_server_async_requestmetrics
                    GROUP BY model
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

        result = {
            "totals": {
                "total_tokens": int(total_tokens or 0),
                "total_requests": int(total_all_requests or 0),
                "total_inference_requests": int(total_inference_requests or 0),
                "successful": int(successful_requests or 0),
                "failed": int(failed_inference or 0),
                "auth_failures": int(auth_failures or 0),
                "success_rate": success_rate,
                "unique_users": int(unique_users or 0),
            },
            "per_model": per_model,
            "time_bounds": None,
        }
        
        # Cache for 30 seconds
        cache.set(cache_key, result, timeout=30)
        return result
    except Exception as e:
        log.error(f"Error fetching realtime metrics: {e}")
        return JsonResponse({"error": str(e)}, status=500)

@router.get("/analytics/logs")
def get_realtime_logs(request, page: int = 0, per_page: int = 500, cluster: str = "all"):
    """Latest AccessLog with optional joined RequestLog and User (LEFT JOIN semantics)."""
    try:
        start_index = page * per_page
        end_index = start_index + per_page

        # OPTIMIZED: LEFT JOIN semantics with metrics for pre-calculated latency
        # Utilize DB indexes: order by indexed timestamp_request desc, then status_code
        qs = (
            AsyncAccessLog.objects
            .select_related("user", "request_log", "request_log__metrics")  # Add metrics
            .only(  # only pull these fields, defer everything else
                "id", "timestamp_request", "status_code", "api_route", "error",
                "user__id", "user__name", "user__username", "user__idp_id", "user__idp_name", "user__auth_service",
                "request_log__id", "request_log__cluster", "request_log__model",
                "request_log__openai_endpoint", "request_log__timestamp_compute_request",
                "request_log__timestamp_compute_response", "request_log__task_uuid",
                "request_log__metrics__response_time_sec"  # Pre-calculated latency
            )
            .defer(  # explicitly defer heavy text fields
                "request_log__prompt", "request_log__result"
            )
        )
        
        # Filter by cluster if specified
        if cluster and cluster.lower() != "all":
            qs = qs.filter(request_log__cluster=cluster.lower())
        
        qs = qs.order_by("-timestamp_request", "-status_code")

        sliced = qs[start_index:end_index]

        results = []
        for al in sliced:
            rl = getattr(al, "request_log", None)
            user = getattr(al, "user", None)

            # OPTIMIZED: Use pre-calculated latency from RequestMetrics with fallback
            latency_secs = None
            if rl:
                # Try to get pre-calculated value first
                try:
                    metrics = getattr(rl, "metrics", None)
                    if metrics and metrics.response_time_sec is not None:
                        latency_secs = metrics.response_time_sec
                except Exception:
                    pass
                
                # Fallback: Calculate if metrics not available (e.g., not processed yet)
                if latency_secs is None and rl.timestamp_compute_response and rl.timestamp_compute_request:
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
                "prompt_snippet": _truncate(getattr(rl, "prompt", "")),
                "result_snippet": _truncate(getattr(rl, "result", "")),
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
def get_users_per_model(request, cluster: str = "all"):
    """Get unique users per model with caching to reduce DB load."""
    try:
        # Check cache first (5 minute TTL)
        cache_key = f"dashboard:users_per_model:{cluster}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached
        
        from django.db import connection
        with connection.cursor() as cursor:
            if cluster and cluster.lower() != "all":
                cursor.execute(
                    """
                    SELECT rl.model, COUNT(DISTINCT al.user_id) AS user_count
                    FROM resource_server_async_requestlog rl
                    JOIN resource_server_async_accesslog al ON al.id = rl.access_log_id
                    WHERE al.user_id IS NOT NULL AND al.user_id <> '' AND rl.cluster = %s
                    GROUP BY rl.model
                    ORDER BY user_count DESC
                    """,
                    [cluster.lower()]
                )
            else:
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
        result = [{"model": r[0], "user_count": int(r[1] or 0)} for r in rows]
        
        # Cache for 30 seconds
        cache.set(cache_key, result, timeout=30)
        return result
    except Exception as e:
        log.error(f"Error fetching users per model: {e}")
        return JsonResponse({"error": str(e)}, status=500)

@router.get("/analytics/users-table")
def get_users_table(request, cluster: str = "all"):
    """Tabular list of users with last access, success/failure counts, success%, last failure time."""
    try:
        # Check cache first (1 minute TTL)
        cache_key = f"dashboard:users_table:{cluster}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached
        
        from django.db import connection
        with connection.cursor() as cursor:
            if cluster and cluster.lower() != "all":
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
                    LEFT JOIN resource_server_async_requestlog rl ON rl.access_log_id = al.id
                    WHERE (rl.cluster = %s OR rl.cluster IS NULL)
                    GROUP BY u.name, u.username
                    HAVING COUNT(rl.id) > 0 OR COUNT(al.id) = 0
                    ORDER BY last_access DESC NULLS LAST, u.username
                    """,
                    [cluster.lower()]
                )
            else:
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
        
        # Cache for 60 seconds
        cache.set(cache_key, results, timeout=60)
        return results
    except Exception as e:
        log.error(f"Error fetching users table: {e}")
        return JsonResponse({"error": str(e)}, status=500)

@router.get("/analytics/series")
def get_overall_series(request, window: str = "24h", cluster: str = "all"):
    try:
        from django.db import connection
        delta, trunc_unit = _parse_series_window(window)
        end_ts = timezone.now()
        start_ts = end_ts - delta
        
        # Build cluster filter condition
        cluster_join = ""
        cluster_filter = ""
        cluster_params = []
        if cluster and cluster.lower() != "all":
            cluster_join = "JOIN resource_server_async_requestlog rl ON rl.access_log_id = al.id"
            cluster_filter = "AND rl.cluster = %s"
            cluster_params = [cluster.lower()]
        
        with connection.cursor() as cursor:
            if cluster and cluster.lower() != "all":
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
                      FROM resource_server_async_accesslog al
                      {cluster_join}
                      WHERE al.timestamp_request >= %s AND al.timestamp_request <= %s {cluster_filter}
                      GROUP BY bucket
                    ) a ON a.bucket = s.bucket
                    ORDER BY s.bucket
                    """,
                    [trunc_unit, start_ts, trunc_unit, end_ts, trunc_unit, trunc_unit, start_ts, end_ts] + cluster_params
                )
            else:
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
        # OPTIMIZED: Removed unnecessary debug query that duplicates the main query
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
    Combines qstat job data (for Sophia/Polaris) or Metis API status with configured endpoints to mark offline models.
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

        # ===== METIS CLUSTER HANDLING =====
        if cluster == "metis":
            # Fetch Metis status
            metis_status, err = async_to_sync(metis_utils.fetch_metis_status)(use_cache=True)
            
            if err or not metis_status:
                # Serve stale cache if available
                cached_payload = cache.get(cache_key)
                if cached_payload:
                    return JsonResponse(cached_payload)
                # Fallback: empty result
                payload = {"items": [], "free_nodes": None}
                cache.set(cache_key, payload, timeout=120)
                return JsonResponse(payload)
            
            # Process Metis status into dashboard format
            items = []
            for model_key, model_info in metis_status.items():
                status_raw = model_info.get("status", "Unknown")
                experts = model_info.get("experts", [])
                description = model_info.get("description", "")
                
                # Map Metis status to dashboard status
                if status_raw == "Live":
                    status = "running"
                elif status_raw == "Stopped":
                    status = "offline"
                else:
                    status = "queued"
                
                # Add each expert model as a separate entry
                for expert in experts:
                    if isinstance(expert, str) and len(expert) > 0:
                        items.append({
                            "model": expert,
                            "status": status,
                            "nodes_reserved": "",
                            "host_name": "",
                            "start_info": description,
                        })
            
            payload = {
                "items": items,
                "free_nodes": None  # Metis doesn't have node info
            }
            # Cache for 2 minutes
            cache.set(cache_key, payload, timeout=120)
            return JsonResponse(payload)

        # ===== QSTAT-BASED CLUSTER HANDLING (Sophia, Polaris) =====
        # Query configured models once (reused in multiple places)
        configured_models = set(Endpoint.objects.filter(cluster=cluster).values_list("model", flat=True))
        
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
        
        if err:
            # Serve stale cache if available
            cached_payload = cache.get(cache_key)
            if cached_payload:
                return JsonResponse(cached_payload)
            # Fallback: mark configured endpoints as offline (reuse configured_models)
            items = [{
                "model": m,
                "status": "offline",
                "nodes_reserved": "",
                "host_name": "",
                "start_info": "",
            } for m in sorted(configured_models)]
            payload = {"items": items, "free_nodes": None}
            # Cache for 2 minutes
            cache.set(cache_key, payload, timeout=120)
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

        # Add offline models (reuse configured_models from earlier)
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
        log.error(f"Error fetching health status for cluster {cluster}: {e}")
        return JsonResponse({"error": str(e)}, status=500)

# ========= Additional realtime endpoints =========
@router.get("/analytics/requests-per-user")
def get_requests_per_user(request, cluster: str = "all"):
    """Overall requests per user (from AccessLog/User)."""
    try:
        # Check cache first (1 minute TTL)
        cache_key = f"dashboard:requests_per_user:{cluster}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached
        
        from django.db import connection
        with connection.cursor() as cursor:
            if cluster and cluster.lower() != "all":
                cursor.execute(
                    """
                    SELECT u.name, u.username,
                           COUNT(*)::bigint AS total,
                           COUNT(*) FILTER (WHERE al.status_code=0 OR al.status_code BETWEEN 200 AND 299) AS successful,
                           COUNT(*) FILTER (WHERE al.status_code >= 300 OR al.status_code IS NULL) AS failed
                    FROM resource_server_async_accesslog al
                    JOIN resource_server_async_user u ON u.id = al.user_id
                    JOIN resource_server_async_requestlog rl ON rl.access_log_id = al.id
                    WHERE rl.cluster = %s
                    GROUP BY u.name, u.username
                    ORDER BY total DESC
                    """,
                    [cluster.lower()]
                )
            else:
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
        
        result = [
            {"name": r[0], "username": r[1], "total": int(r[2] or 0), "successful": int(r[3] or 0), "failed": int(r[4] or 0)}
            for r in rows
        ]
        
        # Cache for 60 seconds
        cache.set(cache_key, result, timeout=60)
        return result
    except Exception as e:
        log.error(f"Error fetching requests per user: {e}")
        return JsonResponse({"error": str(e)}, status=500)

@router.get("/analytics/batch/overview")
def get_batch_overview(request):
    """Batch metrics overview (prefers BatchMetrics, falls back to parsing BatchLog.result)."""
    try:
        # Check cache first (1 minute TTL)
        cache_key = "dashboard:batch_overview"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached
        
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
                    result = {
                        "total_tokens": total_tokens,
                        "total_requests": total_requests,
                        "total_jobs": total_jobs,
                        "completed_jobs": completed_jobs,
                        "success_rate": success_rate,
                    }
                    # Cache for 60 seconds
                    cache.set(cache_key, result, timeout=60)
                    return result
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
        result = {
            "total_tokens": total_tokens,
            "total_requests": total_requests,
            "total_jobs": total_jobs,
            "completed_jobs": completed_jobs,
            "success_rate": success_rate,
        }
        # Cache for 60 seconds
        cache.set(cache_key, result, timeout=60)
        return result
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


@router.get("/analytics/query-logs")
def query_logs_custom(request):
    """Custom log query builder with flexible filters."""
    try:
        from django.db import connection
        import re as regex_module
        
        # Parse query parameters
        rows = int(request.GET.get('rows', 10))
        rows = min(max(1, rows), 10000)  # Clamp between 1 and 10000
        
        status_op = request.GET.get('status_op', '')
        status_val = request.GET.get('status_val', '')
        name_filter = request.GET.get('name', '')
        prompt_filter = request.GET.get('prompt', '')
        api_filter = request.GET.get('api', '')
        cluster_filter = request.GET.get('cluster', '')
        from_ts = request.GET.get('from_ts', '')
        to_ts = request.GET.get('to_ts', '')
        tzname = 'America/Chicago'  # Fixed timezone
        
        # Build WHERE clauses
        conditions = ["1=1"]
        params = []
        
        # Status filter
        if status_op and status_val:
            allowed_ops = ['=', '!=', '>', '<', '>=', '<=']
            if status_op in allowed_ops:
                conditions.append(f"a.status_code {status_op} %s")
                params.append(int(status_val))
        
        # Name filter (ILIKE)
        if name_filter:
            conditions.append("u.name ILIKE %s")
            params.append(name_filter)
        
        # Prompt filter (ILIKE)
        if prompt_filter:
            conditions.append("r.prompt ILIKE %s")
            params.append(prompt_filter)
        
        # API route filter (ILIKE)
        if api_filter:
            conditions.append("a.api_route ILIKE %s")
            params.append(api_filter)
        
        # Cluster filter
        if cluster_filter:
            conditions.append("r.cluster = %s")
            params.append(cluster_filter.lower())
        
        # Timestamp expression
        ts_expr = "COALESCE(r.timestamp_compute_request, a.timestamp_request)"
        
        # Date filters
        if from_ts:
            conditions.append(f"{ts_expr} >= %s::timestamptz")
            params.append(from_ts)
        
        if to_ts:
            conditions.append(f"{ts_expr} <= %s::timestamptz")
            params.append(to_ts)
        
        where_clause = " AND ".join(conditions)
        
        # Build final query
        query = f"""
        SELECT json_agg(row_to_json(t))
        FROM (
            SELECT
                r.id AS request_id,
                r.cluster,
                r.framework,
                r.model,
                r.openai_endpoint,
                r.timestamp_compute_request,
                r.timestamp_compute_response,
                r.prompt,
                r.result,
                r.task_uuid,
                a.id AS accesslog_id,
                a.timestamp_request,
                a.timestamp_response,
                a.api_route,
                a.origin_ip,
                a.status_code,
                a.error,
                u.id AS user_id,
                u.name AS user_name,
                u.username AS user_username,
                u.idp_id,
                u.idp_name,
                u.auth_service
            FROM resource_server_async_accesslog a
            LEFT JOIN resource_server_async_requestlog r
              ON r.access_log_id = a.id
            LEFT JOIN resource_server_async_user u
              ON a.user_id = u.id
            WHERE {where_clause}
            ORDER BY {ts_expr} DESC
            LIMIT %s
        ) t
        """
        
        # Execute query
        with connection.cursor() as cursor:
            # Set timezone first
            cursor.execute("SET TIME ZONE %s", [tzname])
            # Then execute the main query
            cursor.execute(query, params + [rows])
            result = cursor.fetchone()
            
        # Return JSON array or empty array if no results
        data = result[0] if result and result[0] else []
        return JsonResponse({"results": data, "count": len(data) if data else 0}, safe=False)
        
    except Exception as e:
        log.error(f"Error in custom log query: {e}")
        return JsonResponse({"error": str(e)}, status=500)
