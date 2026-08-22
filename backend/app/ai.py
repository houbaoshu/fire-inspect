from __future__ import annotations

import base64
import hashlib
import io
import json
import math
import re
import wave
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from .config import get_settings


@dataclass(frozen=True)
class TranscriptResult:
    start_ms: int
    end_ms: int
    speaker_label: str
    text: str
    confidence: float | None = None


@dataclass(frozen=True)
class FactCandidate:
    title: str
    detail: str | None
    confidence: float
    evidence_indexes: list[int]


class Transcriber(Protocol):
    def transcribe(self, data: bytes, content_type: str) -> list[TranscriptResult]: ...


class VisionAnalyzer(Protocol):
    def analyze(self, images: list[bytes]) -> list[dict[str, Any]]: ...


class OCRProvider(Protocol):
    def extract(self, image: bytes) -> str: ...


class LLMProvider(Protocol):
    def extract_facts(self, transcript: list[str]) -> list[FactCandidate]: ...

    def answer_with_sources(
        self, question: str, sources: list[dict[str, Any]]
    ) -> dict[str, Any]: ...


class EmbeddingProvider(Protocol):
    dimensions: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class MockAI:
    """Deterministic offline provider. It never invents facts or legal sources."""

    dimensions = 32

    def transcribe(self, data: bytes, content_type: str) -> list[TranscriptResult]:
        if content_type.startswith("text/"):
            text = data.decode("utf-8", errors="replace").strip()
            return (
                [TranscriptResult(0, max(1000, len(text) * 80), "说话人 1", text, 1.0)]
                if text
                else []
            )
        return []

    def analyze(self, images: list[bytes]) -> list[dict[str, Any]]:
        return [{"description": "待人工核对的关键画面", "confidence": 0.0} for _ in images]

    def extract(self, image: bytes) -> str:
        return ""

    def extract_facts(self, transcript: list[str]) -> list[FactCandidate]:
        candidates: list[FactCandidate] = []
        for index, text in enumerate(transcript):
            for sentence in re.split(r"[。！？\n]+", text):
                sentence = sentence.strip()
                if len(sentence) >= 4:
                    candidates.append(
                        FactCandidate(
                            title=sentence[:200],
                            detail=None,
                            confidence=0.5,
                            evidence_indexes=[index],
                        )
                    )
        return candidates[:30]

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            values = [0.0] * self.dimensions
            for token in re.findall(r"[\w\u4e00-\u9fff]+", text.lower()):
                digest = hashlib.sha256(token.encode()).digest()
                values[int.from_bytes(digest[:2], "big") % self.dimensions] += 1.0
            norm = math.sqrt(sum(value * value for value in values)) or 1.0
            vectors.append([value / norm for value in values])
        return vectors

    def answer_with_sources(self, question: str, sources: list[dict[str, Any]]) -> dict[str, Any]:
        if not sources:
            return {
                "answer": "未找到可靠依据，请补充或核对知识文档。",
                "possible_issue": None,
                "applicability": None,
            }
        summaries = "；".join(source["text"][:160].strip() for source in sources[:3])
        return {
            "answer": f"根据已上传材料，可参考：{summaries}",
            "possible_issue": question,
            "applicability": "以上内容仅来自列出的材料，适用性需由执法人员确认。",
        }


class OpenAICompatibleAI(MockAI):
    """OpenAI-compatible chat/embedding adapter; media adapters remain independently replaceable."""

    def __init__(self) -> None:
        settings = get_settings()
        if not settings.openai_base_url or not settings.openai_api_key:
            raise RuntimeError("OpenAI-compatible provider is not configured")
        self.base_url = settings.openai_base_url.rstrip("/")
        self.api_key = settings.openai_api_key
        self.llm_model = settings.llm_model
        self.embedding_model = settings.embedding_model
        self.vision_model = settings.vision_model or settings.llm_model
        self.transcription_model = settings.transcription_model

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = httpx.post(
            f"{self.base_url}{path}",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=payload,
            timeout=120,
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = response.text.strip()[:1200]
            raise RuntimeError(
                f"模型服务请求失败（HTTP {response.status_code}）：{detail or '未返回错误详情'}"
            ) from exc
        return response.json()

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not self.embedding_model:
            return super().embed(texts)
        result = self._post(
            "/embeddings", {"model": self.embedding_model, "input": texts, "dimensions": 32}
        )
        return [
            item["embedding"] for item in sorted(result["data"], key=lambda item: item["index"])
        ]

    def transcribe(self, data: bytes, content_type: str) -> list[TranscriptResult]:
        if not self.transcription_model:
            return []
        if "qwen3-asr" in self.transcription_model.lower():
            return self._transcribe_with_audio_input(data, content_type)
        extension = {
            "audio/wav": ".wav",
            "audio/x-wav": ".wav",
            "audio/mpeg": ".mp3",
            "audio/mp4": ".m4a",
            "video/mp4": ".mp4",
        }.get(content_type, ".bin")
        response = httpx.post(
            f"{self.base_url}/audio/transcriptions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            data={"model": self.transcription_model, "response_format": "verbose_json"},
            files={"file": (f"recording{extension}", data, content_type)},
            timeout=600,
        )
        if response.status_code in {404, 405}:
            return self._transcribe_with_audio_input(data, content_type)
        response.raise_for_status()
        payload = response.json()
        segments = payload.get("segments") or []
        if not segments and payload.get("text"):
            segments = [{"start": 0, "end": 0, "text": payload["text"]}]
        return [
            TranscriptResult(
                start_ms=round(float(item.get("start", 0)) * 1000),
                end_ms=round(float(item.get("end", item.get("start", 0))) * 1000),
                speaker_label=f"说话人 {int(item.get('speaker_index', 0)) + 1}",
                text=str(item.get("text", "")).strip(),
                confidence=item.get("confidence"),
            )
            for item in segments
            if str(item.get("text", "")).strip()
        ]

    def _transcribe_with_audio_input(
        self, data: bytes, content_type: str
    ) -> list[TranscriptResult]:
        encoded = base64.b64encode(data).decode()
        result = self._post(
            "/chat/completions",
            {
                "model": self.transcription_model,
                "messages": [
                    {
                        "content": [
                            {
                                "type": "input_audio",
                                "input_audio": {"data": f"data:{content_type};base64,{encoded}"},
                            }
                        ],
                        "role": "user",
                    }
                ],
                "stream": False,
                "asr_options": {"enable_itn": False},
            },
        )
        text = str(result["choices"][0]["message"].get("content") or "").strip()
        if not text:
            return []
        duration_ms = 0
        if content_type in {"audio/wav", "audio/x-wav"}:
            with wave.open(io.BytesIO(data), "rb") as audio:
                duration_ms = round(audio.getnframes() / audio.getframerate() * 1000)
        return [TranscriptResult(0, duration_ms, "说话人 1", text, None)]

    def _vision_text(self, image: bytes, prompt: str) -> str:
        if not self.vision_model:
            return ""
        encoded = base64.b64encode(image).decode()
        result = self._post(
            "/chat/completions",
            {
                "model": self.vision_model,
                "temperature": 0,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/jpeg;base64,{encoded}"},
                            },
                        ],
                    }
                ],
            },
        )
        return str(result["choices"][0]["message"]["content"]).strip()

    def analyze(self, images: list[bytes]) -> list[dict[str, Any]]:
        return [
            {
                "description": self._vision_text(
                    image,
                    "用一句不超过60字的中文，客观描述画面中明确可见的消防检查相关内容；"
                    "不猜测地点、身份或违法结论。",
                ),
                "confidence": 0.5,
            }
            for image in images
        ]

    def extract(self, image: bytes) -> str:
        return self._vision_text(image, "逐字提取图片中可辨认的文字，不补全无法辨认的内容。")

    def _chat_json(self, system: str, user: str) -> dict[str, Any]:
        if not self.llm_model:
            raise RuntimeError("LLM model is not configured")
        result = self._post(
            "/chat/completions",
            {
                "model": self.llm_model,
                "temperature": 0,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
        )
        return json.loads(result["choices"][0]["message"]["content"])

    def extract_facts(self, transcript: list[str]) -> list[FactCandidate]:
        if not transcript:
            return []
        payload = self._chat_json(
            "你只提取输入中明确出现、与消防检查有关且可由原证据直接核对的现场事实。"
            "不推测身份、原因或执法结论；不要把未显示、未观察到、无法确认、画面不清晰"
            "或与消防检查无关的人物外貌和动作作为事实。"
            '输出 JSON：{"facts":[{"title":str,"detail":str|null,'
            '"confidence":0..1,"evidence_indexes":[int]}]}。',
            json.dumps({"evidence_texts": transcript}, ensure_ascii=False),
        )
        candidates: list[FactCandidate] = []
        for item in payload.get("facts", []):
            title = str(item.get("title") or "").strip()
            if not title or any(
                phrase in title for phrase in ("未显示", "未观察到", "无法确认", "画面不清晰")
            ):
                continue
            indexes = [
                int(index)
                for index in item.get("evidence_indexes", [])
                if isinstance(index, int) and 0 <= index < len(transcript)
            ]
            if indexes:
                candidates.append(
                    FactCandidate(
                        title=title[:500],
                        detail=str(item["detail"]) if item.get("detail") else None,
                        confidence=max(0.0, min(1.0, float(item.get("confidence", 0.5)))),
                        evidence_indexes=indexes,
                    )
                )
        return candidates

    def answer_with_sources(self, question: str, sources: list[dict[str, Any]]) -> dict[str, Any]:
        if not sources:
            return super().answer_with_sources(question, sources)
        payload = self._chat_json(
            "你是消防检查参考助手。只能根据输入的 sources 回答；不得补造法规名称、条款或来源。"
            "证据不足时明确说未找到可靠依据。输出 JSON，字段为 answer、possible_issue、"
            "applicability。",
            json.dumps({"question": question, "sources": sources}, ensure_ascii=False),
        )
        return {
            "answer": str(payload.get("answer") or "未找到可靠依据。"),
            "possible_issue": payload.get("possible_issue"),
            "applicability": payload.get("applicability"),
        }


def get_ai() -> MockAI:
    if get_settings().ai_provider == "openai_compatible":
        return OpenAICompatibleAI()
    return MockAI()
