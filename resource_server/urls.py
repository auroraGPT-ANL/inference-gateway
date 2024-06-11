from django.urls import path
from resource_server import views

# URLs to access different Django views
urlpatterns = [
    path("polaris/", views.Polaris.as_view(), name="polaris"),
]