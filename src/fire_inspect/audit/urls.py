from django.urls import re_path

from fire_inspect.audit.views import AuditEventListView

urlpatterns = [
    re_path(r"^admin/audit/?$", AuditEventListView.as_view(), name="admin-audit"),
]
