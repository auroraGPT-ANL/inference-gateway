from django.urls import path
from dashboard_async.views import api

# Use the unique namespace or versioned API instance
urlpatterns = [
    path('', api.urls),  # This will serve all routes under the 'dashboard_async/' URL namespace
]