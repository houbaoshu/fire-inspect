from django.urls import path

from fire_inspect.operations.views import LiveView, ReadyView

urlpatterns = [
    path("health/live", LiveView.as_view(), name="health-live"),
    path("health/ready", ReadyView.as_view(), name="health-ready"),
]
