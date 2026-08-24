"""Replaceable AI capability contracts and safe deterministic adapters."""

from .contracts import (
    ASRProvider,
    AssistantProvider,
    DocumentDraftProvider,
    EmbeddingProvider,
    FactProvider,
    VisionProvider,
)
from .http_provider import GenericHTTPProvider
from .providers import (
    CapabilityUnavailable,
    DisabledProvider,
    FixedResponseProvider,
    ProviderFailure,
)
from .schemas import (
    AssistantAnswer,
    DocumentDraftOutput,
    DocumentPhotoItemDraft,
    EmbeddingOutput,
    EvidenceReference,
    FactCandidate,
    FactExtractionOutput,
    NavigationAction,
    TranscriptOutput,
    TranscriptSegment,
    VisualDescription,
)

__all__ = [
    "ASRProvider",
    "AssistantAnswer",
    "AssistantProvider",
    "CapabilityUnavailable",
    "DisabledProvider",
    "DocumentDraftOutput",
    "DocumentDraftProvider",
    "DocumentPhotoItemDraft",
    "EmbeddingOutput",
    "EmbeddingProvider",
    "EvidenceReference",
    "FactCandidate",
    "FactExtractionOutput",
    "FactProvider",
    "FixedResponseProvider",
    "GenericHTTPProvider",
    "NavigationAction",
    "ProviderFailure",
    "TranscriptOutput",
    "TranscriptSegment",
    "VisionProvider",
    "VisualDescription",
]
