from django.urls import include, path
from rest_framework.routers import DefaultRouter

from fire_inspect.knowledge.views import (
    KnowledgeDocumentViewSet,
    knowledge_clause_detail,
    knowledge_search,
)

router = DefaultRouter(trailing_slash=True)
router.register("knowledge/documents", KnowledgeDocumentViewSet, basename="knowledge-document")

urlpatterns = [
    path("", include(router.urls)),
    path("knowledge/search/", knowledge_search, name="knowledge-search"),
    path(
        "knowledge/documents/<uuid:document_id>/clauses/<uuid:clause_id>/",
        knowledge_clause_detail,
        name="knowledge-clause-detail",
    ),
]
