from rest_framework.views import APIView
from rest_framework.response import Response
from django.db import models
from django.db.models import Count, Avg, F, OuterRef, Subquery, Q, Value
from resource_server.models import Endpoint, Log
from rest_framework.pagination import PageNumberPagination
from rest_framework import filters
from django_filters.rest_framework import DjangoFilterBackend
from django.db.models.functions import Coalesce
import resource_server.utils as utils

class MetricsView(APIView):
    """API view to provide metrics data."""

    def get(self, request, *args, **kwargs):
        # Calculate metrics
        total_requests = Log.objects.count()
        successful_requests = Log.objects.filter(Q(response_status=200) | Q(response_status__isnull=True)).count()
        failed_requests = total_requests - successful_requests

        total_users = Log.objects.values('username').distinct().count()
        user_details = Log.objects.values('name', 'username').distinct()

        gcc = utils.get_compute_client_from_globus_app()
        active_endpoints = Endpoint.objects.all()
        # Endpoint details
        endpoint_details = []
        for endpoint in active_endpoints:
            status = gcc.get_endpoint_status(endpoint.endpoint_uuid)
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
        model_requests = logs_with_annotations.values('model').annotate(
            total_requests=Count('id'),
            successful_requests=Count('id', filter=Q(response_status=200) | Q(response_status__isnull=True)),
            failed_requests=Count('id', filter=~Q(response_status=200) & Q(response_status__isnull=False))
        )

        model_latency = logs_with_annotations.filter(
            Q(response_status=200) | Q(response_status__isnull=True)
        ).values('model').annotate(
            avg_latency=Avg(F('timestamp_response') - F('timestamp_receive'))
        )

        users_per_model = logs_with_annotations.values('model').annotate(
            user_count=Count('name', distinct=True)
        )

        requests_per_user = logs_with_annotations.values('name').annotate(
            total_requests=Count('id'),
            successful_requests=Count('id', filter=Q(response_status=200) | Q(response_status__isnull=True)),
            failed_requests=Count('id', filter=~Q(response_status=200) & Q(response_status__isnull=False))
        )

        metrics = {
            'total_requests': total_requests,
            'request_details': {'successful': successful_requests, 'failed': failed_requests},
            'total_users': total_users,
            'user_details': list(user_details),
            'endpoint_details': endpoint_details,
            'model_requests': list(model_requests),
            'model_latency': list(model_latency),
            'users_per_model': list(users_per_model),
            'requests_per_user': list(requests_per_user),
        }

        return Response(metrics)

class LogPagination(PageNumberPagination):
    page_size = 10000
    page_size_query_param = 'page_size'
    max_page_size = 100000

class LogView(APIView):
    pagination_class = LogPagination
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ['name', 'username', 'response_status']
    ordering_fields = ['timestamp_receive']

    def get(self, request, *args, **kwargs):
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

        logs = Log.objects.annotate(
            latency=F('timestamp_response') - F('timestamp_receive'),
            model=Coalesce(subquery_model, Value('')),
            framework=Coalesce(subquery_framework, Value('')),
            cluster=Coalesce(subquery_cluster, Value(''))
        ).filter(response_status__isnull=False).values(
            'name', 'username', 'model', 'framework', 'cluster',
            'prompt', 'response_status', 'result', 'latency'
        )
        
        # Apply filters
        for backend in list(self.filter_backends):
            logs = backend().filter_queryset(request, logs, self)

        # Paginate results
        page = self.pagination_class().paginate_queryset(logs, request)
        
        # Paginate results
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(logs, request, view=self)
        
        return paginator.get_paginated_response(page)    

class AnalyticsView(APIView):
    """View to render the analytics dashboard."""

    def get(self, request, *args, **kwargs):
        from django.shortcuts import render
        return render(request, 'analytics.html')