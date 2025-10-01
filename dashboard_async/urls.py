from django.urls import path
from dashboard_async.views import (
    api,
    dashboard_login_view,
    dashboard_logout_view,
    dashboard_password_change_view,
    dashboard_password_change_done_view,
)

# Use the unique namespace or versioned API instance
urlpatterns = [
    # Authentication URLs
    path('login/', dashboard_login_view, name='dashboard_login'),
    path('logout/', dashboard_logout_view, name='dashboard_logout'),
    path('password-change/', dashboard_password_change_view, name='dashboard_password_change'),
    path('password-change/done/', dashboard_password_change_done_view, name='dashboard_password_change_done'),
    
    # API URLs (protected by @login_required on individual views)
    path('', api.urls),  # This will serve all routes under the 'dashboard_async/' URL namespace
]