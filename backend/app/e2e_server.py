import os
from pathlib import Path

os.environ.setdefault("FIRE_ENV", "test")
os.environ.setdefault("FIRE_DATABASE_URL", "sqlite:///./.data/e2e.db")
os.environ.setdefault("FIRE_STORAGE_BACKEND", "local")
os.environ.setdefault("FIRE_LOCAL_STORAGE_PATH", ".data/e2e-objects")
os.environ.setdefault("FIRE_TASK_ALWAYS_EAGER", "true")
os.environ.setdefault("FIRE_AI_PROVIDER", "mock")

import uvicorn

from .database import Base, SessionLocal, engine
from .main import app
from .models import User, UserRole
from .security import hash_password


def main() -> None:
    Path(".data").mkdir(exist_ok=True)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        db.add(
            User(
                username="admin",
                display_name="端到端管理员",
                password_hash=hash_password("admin-password"),
                role=UserRole.ADMIN,
            )
        )
        db.commit()
    uvicorn.run(app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
