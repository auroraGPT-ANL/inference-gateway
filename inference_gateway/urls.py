from django.conf import settings
from django.urls import path, include
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView

urlpatterns = [
    # url(r"^$", schema_view),
    path("dashboard/", include("dashboard.urls")),
    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
    # Optional UI:
    path('api/schema/swagger-ui/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
    path('api/schema/redoc/', SpectacularRedocView.as_view(url_name='schema'), name='redoc'),
]

# Batch processing feature
if settings.ENABLE_BATCHES:
    urlpatterns.append(path("bulk_inference/", include("bulk_inference.urls")))

# Main resource_server inference URL
if settings.ENABLE_ASYNC:
    # Django Ninja for asgi
    urlpatterns.append(path("resource_server/", include("resource_server_async.urls")))
else:
    # Django Rest for wsgi 
    urlpatterns.append(path("resource_server/", include("resource_server.urls")))