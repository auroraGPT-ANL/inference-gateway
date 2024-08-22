from django.urls import path, re_path
from .views import api

# URLs to access different Django views
# urlpatterns = [
#     path('metrics', api.urls, name='metrics'),
#     path('analytics', api.urls, name='analytics'),
#     path('logs', api.urls, name='logs'),
# ]

# Use the unique namespace or versioned API instance
urlpatterns = [
    path('', api.urls),  # This will serve all routes under the 'dashboard/' URL namespace
]
