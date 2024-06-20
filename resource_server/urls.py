from django.urls import path
from resource_server import views

# URLs to access different Django views
urlpatterns = [
    path("polaris/<str:framework>/completions/", views.Polaris.as_view(), name="polaris"),
    path("list-endpoints/", views.ListEndpoints.as_view(), name="list-endpoints"),
]