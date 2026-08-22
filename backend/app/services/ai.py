import base64
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

    @staticmethod
    def _url(base_url: str, path: str) -> str:
        return f"{base_url.rstrip('/')}{path}"

    @staticmethod
    def _aliyun_base_url(base_url: str, api_root: str) -> str:
        """Switch an Aliyun workspace URL between compatible and native API roots."""
        normalized = base_url.rstrip("/")
        for suffix in ("/compatible-mode/v1", "/api/v1"):
            if normalized.endswith(suffix):
                normalized = normalized[: -len(suffix)]
                break
        return f"{normalized}{api_root}"

    @staticmethod
    def _audio_data_url(audio_path: Path) -> str:
        encoded = base64.b64encode(audio_path.read_bytes()).decode("ascii")
        return f"data:audio/wav;base64,{encoded}"

    def embeddings(self, texts: list[str]) -> list[list[float]]:
        if not (
            self.settings.embedding_base_url
            and self.settings.ai_api_key
            and self.settings.ai_embedding_model
        ):
            return [local_embedding(text, self.settings.ai_embedding_dimensions) for text in texts]
        response = httpx.post(
            self._url(self.settings.embedding_base_url, "/embeddings"),
            headers={"Authorization": f"Bearer {self.settings.ai_api_key}"},
            json={
                "model": self.settings.ai_embedding_model,
                "input": texts,
                "dimensions": self.settings.ai_embedding_dimensions,
            },
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
            self._url(self.settings.chat_base_url, "/chat/completions"),
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

    def _transcribe_openai(self, audio_path: Path) -> list[dict[str, Any]]:
        with audio_path.open("rb") as audio:
            response = httpx.post(
                self._url(self.settings.transcription_base_url, "/audio/transcriptions"),
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

    def _transcribe_aliyun_multimodal(self, audio_path: Path) -> list[dict[str, Any]]:
        base_url = self._aliyun_base_url(self.settings.transcription_base_url, "/api/v1")
        response = httpx.post(
            self._url(base_url, "/services/aigc/multimodal-generation/generation"),
            headers={
                "Authorization": f"Bearer {self.settings.ai_api_key}",
                "Content-Type": "application/json",
                "X-DashScope-SSE": "disable",
            },
            json={
                "model": self.settings.ai_transcription_model,
                "input": {
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "input_audio",
                                    "input_audio": {"data": self._audio_data_url(audio_path)},
                                }
                            ],
                        }
                    ]
                },
                "parameters": {"format": "wav", "sample_rate": "16000"},
            },
            timeout=600,
        )
        response.raise_for_status()
        payload = response.json()
        output = payload.get("output", {})
        sentence = output.get("sentence", {})
        text = str(output.get("text") or sentence.get("text") or "").strip()
        if not text:
            return []
        duration_ms = int(payload.get("usage", {}).get("duration", 0) * 1000)
        return [
            {
                "start_ms": 0,
                "end_ms": duration_ms or int(sentence.get("end_time", 0)),
                "text": text,
            }
        ]

    def _transcribe_aliyun_qwen(self, audio_path: Path) -> list[dict[str, Any]]:
        base_url = self._aliyun_base_url(
            self.settings.transcription_base_url, "/compatible-mode/v1"
        )
        response = httpx.post(
            self._url(base_url, "/chat/completions"),
            headers={
                "Authorization": f"Bearer {self.settings.ai_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.settings.ai_transcription_model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_audio",
                                "input_audio": {"data": self._audio_data_url(audio_path)},
                            }
                        ],
                    }
                ],
                "stream": False,
                "asr_options": {"enable_itn": False},
            },
            timeout=600,
        )
        response.raise_for_status()
        payload = response.json()
        content = payload.get("choices", [{}])[0].get("message", {}).get("content", "")
        if isinstance(content, list):
            content = "".join(
                str(item.get("text", "")) for item in content if isinstance(item, dict)
            )
        text = str(content).strip()
        if not text:
            return []
        duration_ms = int(payload.get("usage", {}).get("seconds", 0) * 1000)
        return [{"start_ms": 0, "end_ms": duration_ms, "text": text}]

    def transcribe(self, audio_path: Path) -> list[dict[str, Any]]:
        if not (
            self.settings.transcription_base_url
            and self.settings.ai_api_key
            and self.settings.ai_transcription_model
        ):
            return []
        model = self.settings.ai_transcription_model.lower()
        if model.startswith(("qwen-audio-3.0-asr-flash", "fun-asr-flash")):
            return self._transcribe_aliyun_multimodal(audio_path)
        if model.startswith("qwen3-asr-flash-filetrans"):
            raise ValueError(
                "qwen3-asr-flash-filetrans 仅支持异步文件转写；"
                "请配置 qwen3-asr-flash 或 qwen-audio-3.0-asr-flash"
            )
        if model.startswith("qwen3-asr-flash"):
            return self._transcribe_aliyun_qwen(audio_path)
        return self._transcribe_openai(audio_path)


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=True)) / (
        (math.sqrt(sum(a * a for a in left)) or 1.0) * (math.sqrt(sum(b * b for b in right)) or 1.0)
    )


ai_provider = AIProvider()
