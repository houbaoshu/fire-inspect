from django.urls import re_path

from fire_inspect.common.routing import STRICT_UUID_PATTERN
from fire_inspect.transcripts.views import (
    InspectionTranscriptListView,
    MaterialTranscriptView,
    TranscriptDetailView,
    TranscriptHistoryView,
    TranscriptRoleConfirmView,
    TranscriptSegmentDetailView,
)

UUID = STRICT_UUID_PATTERN

urlpatterns = [
    re_path(
        rf"^inspections/(?P<inspection_id>{UUID})/transcripts/?$",
        InspectionTranscriptListView.as_view(),
        name="inspection-transcripts",
    ),
    re_path(
        rf"^materials/(?P<material_id>{UUID})/transcript/?$",
        MaterialTranscriptView.as_view(),
        name="material-transcript",
    ),
    re_path(
        rf"^transcripts/(?P<transcript_id>{UUID})/?$",
        TranscriptDetailView.as_view(),
        name="transcript-detail",
    ),
    re_path(
        rf"^transcripts/(?P<transcript_id>{UUID})/history/?$",
        TranscriptHistoryView.as_view(),
        name="transcript-history",
    ),
    re_path(
        rf"^transcripts/(?P<transcript_id>{UUID})/segments/(?P<segment_id>{UUID})/?$",
        TranscriptSegmentDetailView.as_view(),
        name="transcript-segment-detail",
    ),
    re_path(
        rf"^transcripts/(?P<transcript_id>{UUID})/segments/(?P<segment_id>{UUID})/confirm-role/?$",
        TranscriptRoleConfirmView.as_view(),
        name="transcript-role-confirm",
    ),
]
