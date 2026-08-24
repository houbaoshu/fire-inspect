from __future__ import annotations

from django.db.models import QuerySet

from fire_inspect.inspections.models import Inspection


class InspectionAccessPolicy:
    @staticmethod
    def queryset_for(user, queryset: QuerySet | None = None) -> QuerySet:
        queryset = queryset if queryset is not None else Inspection.objects.all()
        if not getattr(user, "is_authenticated", False):
            return queryset.none()
        if user.role == "admin":
            return queryset
        return queryset.filter(owner=user)

    @classmethod
    def can_access(cls, user, inspection: Inspection) -> bool:
        return cls.queryset_for(user).filter(pk=inspection.pk).exists()
