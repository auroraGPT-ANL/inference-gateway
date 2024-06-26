from django.urls import path, re_path
from resource_server import views

# URLs to access different Django views
urlpatterns = [
    re_path(r"^polaris/(?P<framework>\w+)/v1/(?P<openai_endpoint>chat/completions|completions)/$", views.Polaris.as_view(), name="polaris"),
    path("list-endpoints/", views.ListEndpoints.as_view(), name="list-endpoints"),
]