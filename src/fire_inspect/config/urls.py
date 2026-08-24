from django.urls import include, path

urlpatterns = [
    path("", include("fire_inspect.operations.urls")),
    path("api/v1/", include("fire_inspect.accounts.urls")),
    path("api/v1/", include("fire_inspect.inspections.urls")),
    path("api/v1/", include("fire_inspect.materials.urls")),
    path("api/v1/", include("fire_inspect.jobs.urls")),
    path("api/v1/", include("fire_inspect.audit.urls")),
    path("api/v1/", include("fire_inspect.knowledge.urls")),
    path("api/v1/", include("fire_inspect.templates.urls")),
    path("api/v1/", include("fire_inspect.documents.urls")),
    path("api/v1/", include("fire_inspect.assistant.urls")),
    path("api/v1/", include("fire_inspect.transcripts.urls")),
    path("api/v1/", include("fire_inspect.facts.urls")),
]
