from django.urls import re_path

from fire_inspect.common.routing import STRICT_UUID_PATTERN
from fire_inspect.facts.views import (
    FactEvidenceLinkView,
    FactHistoryView,
    FactTransitionView,
    InspectionEvidenceCandidateListView,
    InspectionFactDetailView,
    InspectionFactListCreateView,
)

UUID = STRICT_UUID_PATTERN

urlpatterns = [
    re_path(
        rf"^inspections/(?P<inspection_id>{UUID})/evidence-candidates/?$",
        InspectionEvidenceCandidateListView.as_view(),
        name="inspection-evidence-candidates",
    ),
    re_path(
        rf"^inspections/(?P<inspection_id>{UUID})/facts/?$",
        InspectionFactListCreateView.as_view(),
        name="inspection-facts",
    ),
    re_path(
        rf"^inspections/(?P<inspection_id>{UUID})/facts/(?P<fact_id>{UUID})/?$",
        InspectionFactDetailView.as_view(),
        name="inspection-fact-detail",
    ),
    re_path(
        rf"^inspections/(?P<inspection_id>{UUID})/facts/(?P<fact_id>{UUID})/(?P<command>confirm|withdraw|restore)/?$",
        FactTransitionView.as_view(),
        name="inspection-fact-transition",
    ),
    re_path(
        rf"^inspections/(?P<inspection_id>{UUID})/facts/(?P<fact_id>{UUID})/evidence/?$",
        FactEvidenceLinkView.as_view(),
        name="inspection-fact-evidence",
    ),
    re_path(
        rf"^inspections/(?P<inspection_id>{UUID})/facts/(?P<fact_id>{UUID})/history/?$",
        FactHistoryView.as_view(),
        name="inspection-fact-history",
    ),
]
