# from ninja import NinjaAPI
# from dashboard.views import router

# api = NinjaAPI()

# # Register routes from the router
# api.add_router("/", router)


from django.urls import path
from dashboard_async.views import api

# Use the unique namespace or versioned API instance
urlpatterns = [
    path('', api.urls),  # This will serve all routes under the 'resource_server_async/' URL namespace
]