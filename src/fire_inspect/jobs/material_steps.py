from __future__ import annotations

from fire_inspect.jobs.models import ProcessingJob

MATERIAL_STEP_KINDS: dict[str, frozenset[str]] = {
    ProcessingJob.TaskType.TRANSCODE: frozenset({"video"}),
    ProcessingJob.TaskType.AUDIO_EXTRACT: frozenset({"video"}),
    ProcessingJob.TaskType.ASR: frozenset({"video", "audio"}),
    ProcessingJob.TaskType.KEYFRAMES: frozenset({"video"}),
    ProcessingJob.TaskType.VISION: frozenset({"video", "image"}),
    ProcessingJob.TaskType.FACT_EXTRACTION: frozenset({"video", "audio", "text"}),
}


def material_steps_for(kind: str) -> tuple[str, ...]:
    return tuple(step for step, kinds in MATERIAL_STEP_KINDS.items() if kind in kinds)


def supports_material_step(*, kind: str, step: str) -> bool:
    return kind in MATERIAL_STEP_KINDS.get(step, frozenset())
