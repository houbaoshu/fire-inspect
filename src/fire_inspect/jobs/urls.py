from django.urls import re_path

from fire_inspect.common.routing import STRICT_UUID_PATTERN
from fire_inspect.jobs.views import (
    AdminJobListView,
    JobDetailView,
    JobRetryView,
    MaterialJobSubmitView,
)

UUID = rf"(?P<job_id>{STRICT_UUID_PATTERN})"
MATERIAL_UUID = rf"(?P<material_id>{STRICT_UUID_PATTERN})"

urlpatterns = [
    re_path(r"^admin/jobs/?$", AdminJobListView.as_view(), name="admin-job-list"),
    re_path(
        rf"^materials/{MATERIAL_UUID}/jobs/(?P<step>[a-z_]+)/?$",
        MaterialJobSubmitView.as_view(),
        name="material-job-submit",
    ),
    re_path(rf"^jobs/{UUID}/?$", JobDetailView.as_view(), name="job-detail"),
    re_path(rf"^jobs/{UUID}/retry/?$", JobRetryView.as_view(), name="job-retry"),
]
