import uuid

from celery import Celery

from .config import get_settings
from .database import SessionLocal
from .models import StepKind
from .processing import process_material

settings = get_settings()
celery_app = Celery("fire_inspect", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.task_always_eager = settings.task_always_eager
celery_app.conf.task_eager_propagates = True


@celery_app.task(name="materials.process")
def process_material_task(material_id: str, only: str | None = None) -> None:
    with SessionLocal() as db:
        process_material(db, uuid.UUID(material_id), StepKind(only) if only else None)


def dispatch_material(material_id: uuid.UUID, only: StepKind | None = None) -> None:
    process_material_task.delay(str(material_id), only.value if only else None)


@celery_app.task(name="knowledge.parse")
def parse_knowledge_task(version_id: str) -> None:
    from .knowledge_service import parse_knowledge_version

    with SessionLocal() as db:
        parse_knowledge_version(db, uuid.UUID(version_id))


def dispatch_knowledge(version_id: uuid.UUID) -> None:
    parse_knowledge_task.delay(str(version_id))


@celery_app.task(name="templates.analyze")
def analyze_template_task(version_id: str) -> None:
    from .template_service import analyze_template

    with SessionLocal() as db:
        analyze_template(db, uuid.UUID(version_id))


def dispatch_template(version_id: uuid.UUID) -> None:
    analyze_template_task.delay(str(version_id))
