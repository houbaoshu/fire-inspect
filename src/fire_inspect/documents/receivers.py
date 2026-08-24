from django.db.models.signals import post_save
from django.dispatch import receiver

from fire_inspect.facts.signals import fact_changed
from fire_inspect.inspections.signals import inspection_shared_fields_changed
from fire_inspect.jobs.models import ProcessingJob
from fire_inspect.materials.signals import material_deleted

from .models import InspectionDocument
from .services import (
    invalidate_for_fact,
    invalidate_for_shared_fields,
    mark_material_source_deleted,
)


@receiver(inspection_shared_fields_changed)
def on_inspection_shared_fields_changed(
    sender, inspection, actor, changed_fields, **kwargs
) -> None:
    invalidate_for_shared_fields(
        inspection=inspection,
        actor=actor,
        changed_fields=changed_fields,
    )


@receiver(material_deleted)
def on_document_material_deleted(sender, material, actor, reason, **kwargs) -> None:
    mark_material_source_deleted(
        material=material,
        actor=actor,
        reason=reason,
    )


@receiver(fact_changed)
def on_document_fact_changed(sender, fact, actor, **kwargs) -> None:
    invalidate_for_fact(fact=fact, actor=actor)


@receiver(post_save, sender=ProcessingJob)
def on_document_generation_job_status(sender, instance: ProcessingJob, **kwargs) -> None:
    del sender, kwargs
    if (
        instance.task_type != ProcessingJob.TaskType.DOCUMENT_GENERATION
        or instance.scope_type != "document"
        or instance.scope_id is None
    ):
        return
    document = InspectionDocument.objects.filter(
        pk=instance.scope_id,
        generator_job_id=instance.id,
    ).first()
    if document is None:
        return
    if instance.status == ProcessingJob.Status.QUEUED:
        status = InspectionDocument.GeneratorStatus.QUEUED
    elif instance.status == ProcessingJob.Status.RUNNING:
        status = InspectionDocument.GeneratorStatus.RUNNING
    elif instance.status == ProcessingJob.Status.SUCCEEDED:
        status = InspectionDocument.GeneratorStatus.SUCCEEDED
    elif instance.status == ProcessingJob.Status.SUPERSEDED:
        status = InspectionDocument.GeneratorStatus.SUPERSEDED
    else:
        status = InspectionDocument.GeneratorStatus.FAILED

    updates = {"generator_status": status}
    if instance.status in {
        ProcessingJob.Status.QUEUED,
        ProcessingJob.Status.RUNNING,
        ProcessingJob.Status.SUCCEEDED,
    }:
        updates["generator_safe_error"] = ""
    elif not document.generator_safe_error:
        updates["generator_safe_error"] = (
            instance.safe_error_message or "文书生成失败，请稍后单独重试。"
        )
    if instance.status in {
        ProcessingJob.Status.FAILED,
        ProcessingJob.Status.SUPERSEDED,
        ProcessingJob.Status.CANCELLED,
    }:
        updates["generation_valid"] = False
    InspectionDocument.objects.filter(pk=document.pk).update(**updates)
