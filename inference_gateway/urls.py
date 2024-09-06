import os
from django.contrib import admin
from django.urls import path, include
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView
from resource_server_async.api import api

# Batch processing feature flag
ENABLE_BATCHES = os.getenv("ENABLE_BATCHES", False) == 'True'


urlpatterns = [
    #path('admin/', admin.site.urls), # We do not want to expose an admin page protected by simple password
    # url(r"^$", schema_view),
    path("resource_server/", include("resource_server.urls")),
    path("dashboard/", include("dashboard.urls")),
    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
    # Optional UI:
    path('api/schema/swagger-ui/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
    path('api/schema/redoc/', SpectacularRedocView.as_view(url_name='schema'), name='redoc'),

    # Django Ninja async URL
    path("api/", api.urls),
]

if ENABLE_BATCHES:
    urlpatterns.append(path("bulk_inference/", include("bulk_inference.urls")))