from django.urls import path, re_path
from dashboard import views

# URLs to access different Django views
urlpatterns = [
    path('metrics', views.MetricsView.as_view(), name='metrics'),
    path('analytics', views.AnalyticsView.as_view(), name='analytics'),
    path('logs', views.LogView.as_view(), name='logs'),
]