from django.db import models
from django.db.models import Count, Avg, F, OuterRef, Subquery, Q, Value
from django.db.models.functions import Coalesce
from django.utils.timezone import now
from datetime import timedelta
# Async tools
from asgiref.sync import sync_to_async
import asyncio

from resource_server.models import Endpoint, Log
from resource_server.utils import get_compute_client_from_globus_app

#Pagination
from ninja.pagination import paginate
from ninja.pagination import PageNumberPagination as NinjaPageNumberPagination


# Ninja API
from ninja import NinjaAPI, Router
api = NinjaAPI(urls_namespace='dashboard_async_api')
router = Router()


router = Router()

# Metrics API
@router.get("/metrics")
async def metrics_view(request):
    """Asynchronous API to provide metrics data."""

    # Aggregate metrics using async ORM calls
    total_requests = await sync_to_async(Log.objects.count)()
    successful_requests = await sync_to_async(
        Log.objects.filter(Q(response_status=200) | Q(response_status__isnull=True)).count
    )()
    failed_requests = total_requests - successful_requests

    total_users = await sync_to_async(Log.objects.values('username').distinct().count)()
    user_details = await sync_to_async(list)(Log.objects.values('name', 'username').distinct())

    # Async endpoint status gathering
    gcc = get_compute_client_from_globus_app()
    active_endpoints = await sync_to_async(list)(Endpoint.objects.all())
    endpoint_details = [
        {
            'model': endpoint.model,
            'cluster': endpoint.cluster,
            'status': (await sync_to_async(gcc.get_endpoint_status)(endpoint.endpoint_uuid))['status'],
            'framework': endpoint.framework,
        }
        for endpoint in active_endpoints
    ]


    # Annotated logs and aggregated data
    endpoint_models = Endpoint.objects.filter(endpoint_slug=OuterRef('endpoint_slug')).values('model')[:1]
    endpoint_clusters = Endpoint.objects.filter(endpoint_slug=OuterRef('endpoint_slug')).values('cluster')[:1]

    logs_with_annotations = Log.objects.annotate(
        model=Subquery(endpoint_models, output_field=models.CharField()),
        cluster=Subquery(endpoint_clusters, output_field=models.CharField())
    )

    # Aggregated metrics
    model_requests = await sync_to_async(list)(
        logs_with_annotations.values('model').annotate(
            total_requests=Count('id'),
            successful_requests=Count('id', filter=Q(response_status=200) | Q(response_status__isnull=True)),
            failed_requests=Count('id', filter=~Q(response_status=200) & Q(response_status__isnull=False))
        )
    )
    model_latency = await sync_to_async(list)(
        logs_with_annotations.filter(
            Q(response_status=200) | Q(response_status__isnull=True)
        ).values('model').annotate(
            avg_latency=Avg(F('timestamp_response') - F('timestamp_receive'))
        )
    )

    users_per_model = await sync_to_async(list)(
        logs_with_annotations.values('model').annotate(
            user_count=Count('name', distinct=True)
        )
    )

    three_months_ago = now() - timedelta(days=90)
    start_date = three_months_ago - timedelta(days=three_months_ago.weekday())
    weekly_usage = await sync_to_async(list)(
        logs_with_annotations.filter(
            timestamp_receive__gte=start_date
        ).annotate(
            week_start=TruncWeek('timestamp_receive')
        ).values('week_start').annotate(
            request_count=Count('id')
        ).order_by('week_start')
    )

    metrics = {
        'total_requests': total_requests,
        'request_details': {'successful': successful_requests, 'failed': failed_requests},
        'total_users': total_users,
        'user_details': user_details,
        'endpoint_details': endpoint_details,
        'model_requests': model_requests,
        'model_latency': model_latency,
        'users_per_model': users_per_model,
        'weekly_usage': weekly_usage,
    }

    return metrics


# Logs API with Ninja Pagination
@router.get("/logs", response=NinjaPageNumberPagination)
@paginate(NinjaPageNumberPagination)
async def logs_view(request, name: str = None, username: str = None, response_status: int = None):
    """Asynchronous API to provide log data with filters."""
    query = Log.objects.annotate(
        latency=F('timestamp_response') - F('timestamp_receive'),
        model=Subquery(
            Endpoint.objects.filter(endpoint_slug=OuterRef('endpoint_slug')).values('model')[:1]
        ),
        framework=Subquery(
            Endpoint.objects.filter(endpoint_slug=OuterRef('endpoint_slug')).values('framework')[:1]
        ),
        cluster=Subquery(
            Endpoint.objects.filter(endpoint_slug=OuterRef('endpoint_slug')).values('cluster')[:1]
        )
    ).values(
        'name', 'username', 'model', 'framework', 'cluster',
        'prompt', 'response_status', 'result', 'latency'
    )

    # Apply filters
    if name:
        query = query.filter(name=name)
    if username:
        query = query.filter(username=username)
    if response_status:
        query = query.filter(response_status=response_status)

    logs = await sync_to_async(list)(query)
    return logs


# Analytics view (render HTML)
@router.get("/analytics")
async def analytics_view(request):
    """Render analytics HTML page."""
    from django.shortcuts import render
    return render(request, 'analytics.html')