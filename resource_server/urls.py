from django.urls import path
from resource_server import views

# URLs to access different Django views
urlpatterns = [
    path("vllm/", views.VLLM.as_view(), name="vllm",)
]