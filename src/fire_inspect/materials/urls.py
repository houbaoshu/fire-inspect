from django.urls import re_path

from fire_inspect.common.routing import STRICT_UUID_PATTERN
from fire_inspect.materials.views import (
    InspectionMaterialListCreateView,
    MaterialDerivativeDownloadView,
    MaterialDerivativePreviewView,
    MaterialDetailView,
    MaterialDownloadView,
    MaterialPlayView,
    MaterialPreviewView,
)

UUID = rf"(?P<material_id>{STRICT_UUID_PATTERN})"
INSPECTION_UUID = rf"(?P<inspection_id>{STRICT_UUID_PATTERN})"

urlpatterns = [
    re_path(
        rf"^inspections/{INSPECTION_UUID}/materials/?$",
        InspectionMaterialListCreateView.as_view(),
        name="inspection-materials",
    ),
    re_path(rf"^materials/{UUID}/?$", MaterialDetailView.as_view(), name="material-detail"),
    re_path(
        rf"^materials/{UUID}/download/?$",
        MaterialDownloadView.as_view(),
        name="material-download",
    ),
    re_path(
        rf"^materials/{UUID}/preview/?$",
        MaterialPreviewView.as_view(),
        name="material-preview",
    ),
    re_path(rf"^materials/{UUID}/play/?$", MaterialPlayView.as_view(), name="material-play"),
    re_path(
        rf"^material-derivatives/(?P<derivative_id>{STRICT_UUID_PATTERN})/preview/?$",
        MaterialDerivativePreviewView.as_view(),
        name="material-derivative-preview",
    ),
    re_path(
        rf"^material-derivatives/(?P<derivative_id>{STRICT_UUID_PATTERN})/download/?$",
        MaterialDerivativeDownloadView.as_view(),
        name="material-derivative-download",
    ),
]
