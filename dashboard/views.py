from ninja import NinjaAPI, Router
from asgiref.sync import sync_to_async
from resource_server.models import Endpoint, Log
from django.db.models import Count, Avg, F, OuterRef, Subquery, Q, Value
from django.db.models.functions import Coalesce
from django.shortcuts import render
import resource_server.utils as utils
import asyncio
from django.db import models
from rest_framework.pagination import PageNumberPagination
from typing import List, Any


api = NinjaAPI(urls_namespace='dashboard_api')

router = Router()

@router.get("/metrics")
async def metrics_view(request):
    # Calculate metrics
    total_requests = await sync_to_async(Log.objects.count)()
    successful_requests = await sync_to_async(
        Log.objects.filter(Q(response_status=200) | Q(response_status__isnull=True)).count
    )()
    failed_requests = total_requests - successful_requests

    total_users = await sync_to_async(Log.objects.values('username').distinct().count)()
    user_details = await sync_to_async(list)(
        Log.objects.values('name', 'username').distinct()
    )

    gcc = utils.get_compute_client_from_globus_app()
    active_endpoints = await sync_to_async(list)(Endpoint.objects.all())

    # Endpoint details
    endpoint_details = []
    for endpoint in active_endpoints:
        status = await sync_to_async(gcc.get_endpoint_status)(endpoint.endpoint_uuid)
        endpoint_details.append({
            'model': endpoint.model,
            'cluster': endpoint.cluster,
            'status': status['status'],
            'framework': endpoint.framework
        })

    # Subqueries for the metrics
    endpoint_models = Endpoint.objects.filter(endpoint_slug=OuterRef('endpoint_slug')).values('model')[:1]
    endpoint_clusters = Endpoint.objects.filter(endpoint_slug=OuterRef('endpoint_slug')).values('cluster')[:1]

    # Annotate Log entries with model and cluster from the Endpoint table
    logs_with_annotations = Log.objects.annotate(
        model=Subquery(endpoint_models, output_field=models.CharField()),
        cluster=Subquery(endpoint_clusters, output_field=models.CharField())
    )

    # Aggregated data
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

    requests_per_user = await sync_to_async(list)(
        logs_with_annotations.values('name').annotate(
            total_requests=Count('id'),
            successful_requests=Count('id', filter=Q(response_status=200) | Q(response_status__isnull=True)),
            failed_requests=Count('id', filter=~Q(response_status=200) & Q(response_status__isnull=False))
        )
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
        'requests_per_user': requests_per_user,
    }

    return metrics

class LogPagination(PageNumberPagination):
    page_size = 10000
    page_size_query_param = 'page_size'
    max_page_size = 100000

def custom_paginate(queryset: List[Any], page: int, page_size: int):
    """Paginate a queryset manually."""
    start = (page - 1) * page_size
    end = start + page_size
    return queryset[start:end], len(queryset)

@router.get("/logs")
async def log_view(request, name: str = None, username: str = None, response_status: int = None, page: int = 1, page_size: int = 10):
    # Subqueries to fetch related fields from Endpoint model
    subquery_model = Subquery(
        Endpoint.objects.filter(endpoint_slug=OuterRef('endpoint_slug')).values('model')[:1]
    )
    subquery_framework = Subquery(
        Endpoint.objects.filter(endpoint_slug=OuterRef('endpoint_slug')).values('framework')[:1]
    )
    subquery_cluster = Subquery(
        Endpoint.objects.filter(endpoint_slug=OuterRef('endpoint_slug')).values('cluster')[:1]
    )

    logs = await sync_to_async(list)(Log.objects.annotate(
        latency=F('timestamp_response') - F('timestamp_receive'),
        model=Coalesce(subquery_model, Value('')),
        framework=Coalesce(subquery_framework, Value('')),
        cluster=Coalesce(subquery_cluster, Value(''))
    ).filter(response_status__isnull=False).values(
        'name', 'username', 'model', 'framework', 'cluster',
        'prompt', 'response_status', 'result', 'latency'
    ))

    if name:
        logs = [log for log in logs if log['name'] == name]
    if username:
        logs = [log for log in logs if log['username'] == username]
    if response_status:
        logs = [log for log in logs if log['response_status'] == response_status]

    paginated_logs, total_logs = custom_paginate(logs, page, page_size)

    return {
        "results": paginated_logs,
        "page": page,
        "total_pages": (total_logs + page_size - 1) // page_size  # Calculate total pages
    }

@router.get("/analytics")
async def analytics_view(request):
    return render(request, 'analytics.html')

api.add_router("/", router)
