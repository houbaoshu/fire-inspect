from rest_framework.generics import ListAPIView

from fire_inspect.audit.models import AuditEvent
from fire_inspect.audit.serializers import AuditEventSerializer
from fire_inspect.common.permissions import IsAdministrator


class AuditEventListView(ListAPIView):
    permission_classes = [IsAdministrator]
    serializer_class = AuditEventSerializer
    pagination_class = None

    def get_queryset(self):
        return AuditEvent.objects.select_related("actor").order_by("-occurred_at", "-id")
