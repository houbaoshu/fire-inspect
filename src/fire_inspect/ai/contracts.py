from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Protocol
from uuid import UUID

from .schemas import (
    AssistantAnswer,
    DocumentDraftOutput,
    EmbeddingOutput,
    FactExtractionOutput,
    TranscriptOutput,
    VisualDescription,
)


class ASRProvider(Protocol):
    def transcribe(
        self, material_id: UUID, media_path: Path, duration_ms: int
    ) -> TranscriptOutput: ...


class VisionProvider(Protocol):
    def describe(
        self, material_id: UUID, frames: Sequence[tuple[int, Path]]
    ) -> tuple[VisualDescription, ...]: ...


class FactProvider(Protocol):
    def extract_facts(
        self,
        transcript: TranscriptOutput,
        visual_descriptions: Sequence[VisualDescription],
    ) -> FactExtractionOutput: ...


class DocumentDraftProvider(Protocol):
    def draft_document(
        self, document_type: str, context: dict[str, object]
    ) -> DocumentDraftOutput: ...


class EmbeddingProvider(Protocol):
    def embed(self, texts: Sequence[str]) -> tuple[EmbeddingOutput, ...]: ...


class AssistantProvider(Protocol):
    def answer(self, question: str, context: dict[str, object]) -> AssistantAnswer: ...
