import uuid
from typing import Any

from sqlalchemy.orm import Session

from .models import AuditEvent, User


def record_audit(
    db: Session,
    actor: User,
    action: str,
    subject_type: str,
    subject_id: uuid.UUID,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> None:
    db.add(
        AuditEvent(
            actor_id=actor.id,
            action=action,
            subject_type=subject_type,
            subject_id=subject_id,
            before=before,
            after=after,
        )
    )
