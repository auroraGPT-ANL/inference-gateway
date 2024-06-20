from django.urls import path
from .views import Batches

# URLs to access different Django views
urlpatterns = [
    path("batches", Batches.as_view(), name="batches"),
    path("batches/<uuid:id>/", Batches.as_view(), name="batch-detail"),
]