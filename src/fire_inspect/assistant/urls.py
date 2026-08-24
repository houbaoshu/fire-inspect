from django.urls import path

from fire_inspect.assistant.views import AssistantQueryView

urlpatterns = [path("assistant/query", AssistantQueryView.as_view(), name="assistant-query")]
