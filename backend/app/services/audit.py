from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditEvent


def audit(
    db: Session,
    *,
    actor_id: str | None,
    action: str,
    target_type: str,
    target_id: str,
    inspection_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    db.add(
        AuditEvent(
            actor_id=actor_id,
            inspection_id=inspection_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            details=details or {},
        )
    )
