"""Provider-neutral JSON/HTTP adapter.

The adapter deliberately defines a small stable wire contract instead of
binding the product to a vendor SDK.  Deployments can place a local gateway in
front of any ASR, LLM, vision, or embedding implementation.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any
from uuid import UUID

from .config import CapabilitySettings
from .providers import ProviderFailure
from .schemas import (
    AssistantAnswer,
    AssistantCitation,
    DocumentDraftOutput,
    DocumentPhotoItemDraft,
    EmbeddingOutput,
    EvidenceReference,
    FactCandidate,
    FactExtractionOutput,
    NavigationAction,
    NavigationKind,
    SemanticRole,
    TranscriptOutput,
    TranscriptSegment,
    VisualDescription,
)


class GenericHTTPProvider:
    def __init__(self, settings: CapabilitySettings, *, client=None) -> None:
        if settings.provider != "http":
            raise ValueError("GenericHTTPProvider 只接受 http 配置")
        self.settings = settings
        self._client = client

    def _post(self, *, json_payload: dict[str, object], files=None) -> dict[str, Any]:
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover - startup preflight covers it
            raise ProviderFailure("缺少 AI HTTP 客户端组件") from exc
        own_client = self._client is None
        client = self._client or httpx.Client(timeout=self.settings.timeout_seconds)
        headers = {
            "Authorization": f"Bearer {self.settings.api_key}",
            "X-Fire-Inspect-Model": self.settings.model,
        }
        try:
            if files is None:
                response = client.post(
                    self.settings.endpoint,
                    json=json_payload,
                    headers=headers,
                )
            else:
                response = client.post(
                    self.settings.endpoint,
                    data={"request": __import__("json").dumps(json_payload, ensure_ascii=False)},
                    files=files,
                    headers=headers,
                )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError
            return payload
        except httpx.TimeoutException as exc:
            raise ProviderFailure("AI 服务超时，可稍后单独重试") from exc
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in {401, 403}:
                message = "AI 服务鉴权失败，请管理员检查配置"
            elif exc.response.status_code == 429:
                message = "AI 服务繁忙或限流，可稍后单独重试"
            else:
                message = "AI 服务请求失败，可稍后单独重试"
            raise ProviderFailure(message) from exc
        except (ValueError, TypeError, KeyError) as exc:
            # Never include response bodies: they can contain full transcripts,
            # prompts, provider diagnostics, or secrets.
            raise ProviderFailure("AI 服务返回格式无效，未保存本次结果") from exc
        except httpx.RequestError as exc:
            raise ProviderFailure("AI 服务不可用，可稍后单独重试") from exc
        finally:
            if own_client:
                client.close()

    def transcribe(self, material_id: UUID, media_path: Path, duration_ms: int) -> TranscriptOutput:
        try:
            with media_path.open("rb") as stream:
                payload = self._post(
                    json_payload={
                        "capability": "asr",
                        "material_id": str(material_id),
                        "duration_ms": duration_ms,
                        "model": self.settings.model,
                    },
                    files={"media": ("media.bin", stream, "application/octet-stream")},
                )
        except OSError as exc:
            raise ProviderFailure("无法读取待转写材料") from exc
        try:
            segments = tuple(
                TranscriptSegment(
                    material_id=material_id,
                    start_ms=int(item["start_ms"]),
                    end_ms=int(item["end_ms"]),
                    text=str(item["text"]),
                    suggested_role=SemanticRole(item.get("suggested_role", "unknown")),
                )
                for item in payload["segments"]
            )
            output = TranscriptOutput(str(payload["full_text"]), segments)
            output.validate_timeline(duration_ms)
            return output
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderFailure("AI 服务返回格式无效，未保存本次结果") from exc

    def describe(
        self, material_id: UUID, frames: Sequence[tuple[int, Path]]
    ) -> tuple[VisualDescription, ...]:
        opened = []
        try:
            files = []
            for index, (_timestamp_ms, path) in enumerate(frames):
                stream = path.open("rb")
                opened.append(stream)
                files.append(("frames", (f"frame-{index}.jpg", stream, "image/jpeg")))
            payload = self._post(
                json_payload={
                    "capability": "vision",
                    "material_id": str(material_id),
                    "timestamps_ms": [timestamp for timestamp, _ in frames],
                    "model": self.settings.model,
                },
                files=files,
            )
        except OSError as exc:
            raise ProviderFailure("无法读取关键画面") from exc
        finally:
            for stream in opened:
                stream.close()
        allowed_timestamps = {timestamp for timestamp, _ in frames}
        try:
            descriptions = tuple(
                VisualDescription(
                    material_id,
                    int(item["timestamp_ms"]),
                    str(item["text"]),
                )
                for item in payload["descriptions"]
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderFailure("AI 服务返回格式无效，未保存本次结果") from exc
        if any(item.timestamp_ms not in allowed_timestamps for item in descriptions):
            raise ProviderFailure("视觉服务返回了不存在的画面引用")
        return descriptions

    def extract_facts(
        self,
        transcript: TranscriptOutput,
        visual_descriptions: Sequence[VisualDescription],
    ) -> FactExtractionOutput:
        payload = self._post(
            json_payload={
                "capability": "fact_extraction",
                "model": self.settings.model,
                "untrusted_data": {
                    "full_text": transcript.full_text,
                    "segments": [
                        {
                            "material_id": str(item.material_id),
                            "start_ms": item.start_ms,
                            "end_ms": item.end_ms,
                            "text": item.text,
                        }
                        for item in transcript.segments
                    ],
                    "visual_descriptions": [
                        {
                            "material_id": str(item.material_id),
                            "timestamp_ms": item.timestamp_ms,
                            "text": item.text,
                        }
                        for item in visual_descriptions
                    ],
                },
            }
        )
        try:
            facts = tuple(
                FactCandidate(
                    content=str(item["content"]),
                    evidence=tuple(
                        EvidenceReference(
                            UUID(reference["material_id"]),
                            int(reference["start_ms"]),
                            int(reference["end_ms"]),
                        )
                        for reference in item.get("evidence", [])
                    ),
                    needs_verification=bool(item.get("needs_verification", False)),
                    verification_note=str(item.get("verification_note", "")),
                    is_final_enforcement_conclusion=bool(
                        item.get("is_final_enforcement_conclusion", False)
                    ),
                )
                for item in payload["facts"]
            )
            return FactExtractionOutput(facts)
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderFailure("AI 服务返回格式无效，未保存本次结果") from exc

    def draft_document(self, document_type: str, context: dict[str, object]) -> DocumentDraftOutput:
        payload = self._post(
            json_payload={
                "capability": "document_drafting",
                "document_type": document_type,
                "model": self.settings.model,
                "untrusted_data": context,
            }
        )
        try:
            raw_fields = payload["fields"]
            if not isinstance(raw_fields, dict):
                raise ValueError
            return DocumentDraftOutput(
                document_type=str(payload["document_type"]),
                fields={str(key): value for key, value in raw_fields.items()},
                referenced_fact_ids=tuple(
                    UUID(str(value)) for value in payload.get("referenced_fact_ids", [])
                ),
                photo_items=tuple(
                    DocumentPhotoItemDraft(
                        source_id=UUID(str(item["source_id"])),
                        reflected_content=str(item["reflected_content"]),
                        description=str(item.get("description", "")),
                    )
                    for item in payload.get("photo_items", [])
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderFailure("AI 服务返回格式无效，未保存本次结果") from exc

    def embed(self, texts: Sequence[str]) -> tuple[EmbeddingOutput, ...]:
        payload = self._post(
            json_payload={
                "capability": "embedding",
                "model": self.settings.model,
                "untrusted_data": {"texts": list(texts)},
            }
        )
        try:
            outputs = tuple(
                EmbeddingOutput(
                    tuple(float(value) for value in item["values"]),
                    str(item["model_fingerprint"]),
                )
                for item in payload["embeddings"]
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderFailure("AI 服务返回格式无效，未保存本次结果") from exc
        if len(outputs) != len(texts):
            raise ProviderFailure("Embedding 返回数量与输入不一致")
        return outputs

    def answer(self, question: str, context: dict[str, object]) -> AssistantAnswer:
        payload = self._post(
            json_payload={
                "capability": "assistant",
                "model": self.settings.model,
                "question": question,
                "context": context,
            }
        )
        try:
            return AssistantAnswer(
                text=str(payload["text"]),
                citations=tuple(
                    AssistantCitation(
                        str(item["source_type"]),
                        UUID(item["source_id"]),
                        str(item["excerpt"]),
                    )
                    for item in payload.get("citations", [])
                ),
                navigation=tuple(
                    NavigationAction(
                        NavigationKind(item["kind"]),
                        UUID(item["target_id"]),
                        UUID(item["material_id"]) if item.get("material_id") else None,
                        int(item["start_ms"]) if item.get("start_ms") is not None else None,
                    )
                    for item in payload.get("navigation", [])
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderFailure("AI 服务返回格式无效，未保存本次结果") from exc
