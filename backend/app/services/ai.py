import hashlib
import json
import math
from pathlib import Path
from typing import Any

import httpx

from app.core.config import get_settings


def local_embedding(text: str, dimensions: int = 384) -> list[float]:
    """不泄露数据的确定性词袋向量；用于无外部模型时的可运行检索。"""
    values = [0.0] * dimensions
    normalized = "".join(text.lower().split())
    tokens = [normalized[i : i + 2] for i in range(max(1, len(normalized) - 1))]
    for token in tokens:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % dimensions
        values[index] += -1.0 if digest[4] & 1 else 1.0
    norm = math.sqrt(sum(item * item for item in values)) or 1.0
    return [item / norm for item in values]


class AIProvider:
    def __init__(self):
        self.settings = get_settings()

    def _url(self, path: str) -> str:
        return f"{self.settings.ai_base_url.rstrip('/')}{path}"

    def embeddings(self, texts: list[str]) -> list[list[float]]:
        if not (
            self.settings.ai_base_url
            and self.settings.ai_api_key
            and self.settings.ai_embedding_model
        ):
            return [local_embedding(text, self.settings.ai_embedding_dimensions) for text in texts]
        response = httpx.post(
            self._url("/embeddings"),
            headers={"Authorization": f"Bearer {self.settings.ai_api_key}"},
            json={"model": self.settings.ai_embedding_model, "input": texts},
            timeout=60,
        )
        response.raise_for_status()
        vectors = [item["embedding"] for item in response.json()["data"]]
        if any(len(vector) != self.settings.ai_embedding_dimensions for vector in vectors):
            raise ValueError(f"向量模型必须返回 {self.settings.ai_embedding_dimensions} 维向量")
        return vectors

    def chat_json(self, *, system: str, user: str | list[dict[str, Any]]) -> dict[str, Any] | None:
        if not self.settings.ai_enabled:
            return None
        response = httpx.post(
            self._url("/chat/completions"),
            headers={"Authorization": f"Bearer {self.settings.ai_api_key}"},
            json={
                "model": self.settings.ai_chat_model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0,
            },
            timeout=120,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        return json.loads(content)

    def transcribe(self, audio_path: Path) -> list[dict[str, Any]]:
        if not (
            self.settings.ai_base_url
            and self.settings.ai_api_key
            and self.settings.ai_transcription_model
        ):
            return []
        with audio_path.open("rb") as audio:
            response = httpx.post(
                self._url("/audio/transcriptions"),
                headers={"Authorization": f"Bearer {self.settings.ai_api_key}"},
                files={"file": (audio_path.name, audio, "audio/wav")},
                data={
                    "model": self.settings.ai_transcription_model,
                    "response_format": "verbose_json",
                    "timestamp_granularities[]": "segment",
                },
                timeout=600,
            )
        response.raise_for_status()
        payload = response.json()
        return [
            {
                "start_ms": int(segment.get("start", 0) * 1000),
                "end_ms": int(segment.get("end", 0) * 1000),
                "text": segment.get("text", "").strip(),
            }
            for segment in payload.get("segments", [])
            if segment.get("text", "").strip()
        ]


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=True)) / (
        (math.sqrt(sum(a * a for a in left)) or 1.0) * (math.sqrt(sum(b * b for b in right)) or 1.0)
    )


ai_provider = AIProvider()
