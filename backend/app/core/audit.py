"""操作留痕（澄清 §7）：谁/何时/对哪个对象/改了什么（旧值→新值）。"""
from sqlalchemy.orm import Session
from .models import AuditLog, User


def audit(db: Session, actor: User | None, action: str, object_type: str, object_id: int,
          field: str = "", old=None, new=None, note: str = ""):
    db.add(AuditLog(
        actor_id=actor.id if actor else None,
        action=action, object_type=object_type, object_id=object_id,
        detail={"field": field, "old": old, "new": new, "note": note},
    ))
