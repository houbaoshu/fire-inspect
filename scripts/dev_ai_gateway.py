#!/usr/bin/env python3
"""Local development gateway bridging the fire-inspect AI contract to DashScope.

The application core speaks a vendor-neutral internal HTTP protocol
(``capability`` field, multipart media upload, time-anchored responses).
This gateway implements that protocol on 127.0.0.1 and translates each
capability into DashScope OpenAI-compatible calls, so the authorized
real-AI acceptance gate can exercise the production HTTP adapter end to
end without vendor-specific code in the application.

Usage:
    set -a; source .env; set +a
    uv run --frozen python scripts/dev_ai_gateway.py --port 8787

Configuration (process environment only; secrets are never logged):
    DASHSCOPE_API_KEY      upstream credential (required)
    DASHSCOPE_BASE_URL     OpenAI-compatible base URL
    ASR_WINDOW_SEC         transcription window length (default 30)
    ASR_OVERLAP_SEC        overlap between adjacent windows (default 2)
    EMBED_DIM              embedding dimension passed upstream (optional)

Only ``asr``, ``vision``, ``fact_extraction`` and ``embedding`` are
implemented: those are the capabilities exercised by the real-AI gate.
Assistant answers and document drafting are intentionally out of scope.
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from email.parser import BytesParser
from email.policy import default as email_policy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger("dev-ai-gateway")

SILENCE_MARKER = "（无语音内容）"

_VISION_SYSTEM_PROMPT = (
    "你是消防监督检查现场的图像分析助手。"
    "对每一帧画面，客观描述可见的场景、区域、物品堆放、人员动作和消防设施的可见状态。"
    "只允许描述画面中直接可见的内容：不得推测人员身份、职务或姓名，"
    "不得推断画面外或被遮挡的设施状态，不得下执法结论。"
    '只输出 JSON：{"frames": [{"index": <帧序号>, "description": "<一句客观描述>"}]}。'
)

_FACT_SYSTEM_PROMPT = (
    "你是消防监督检查的事实整理助手。输入是一次检查的语音转写片段"
    "（含 material_id、start_ms、end_ms）和关键画面描述（含 material_id、timestamp_ms）。\n"
    "规则：\n"
    "1. 只提取输入材料中明确陈述或直接可见的事实，按业务含义合并为候选事实；\n"
    "1a. 事实内容必须尽量沿用转写原文的措辞，逐句引用或只做最小改写；"
    "禁止加入原文没有的词语（特别是人名、时间词和程度词）；\n"
    "2. 不得推断人员身份、姓名、证件号码；原文未明确说出的一律不写；\n"
    "3. 不得生成罚款、处罚决定等最终执法结论，is_final_enforcement_conclusion 恒为 false；\n"
    "4. 对值班、持证等无法从材料确认的事项，用“需核实/待核实”表述，"
    "并将 needs_verification 置为 true、在 verification_note 说明待核实内容；\n"
    "4a. 涉及值班人员不在岗或持证情况不明时，分别输出两条独立事实："
    "一条说明不在岗/休息（含场所名称，如“消防控制室值班人员休息、不在岗”），"
    "一条说明值班或持证情况需核实（含场所名称，如“消防控制室值班人员持证情况需核实”，"
    "needs_verification 置为 true）；不要把“需核实”只写在 verification_note 里；\n"
    "4b. “值班人员”“负责人”等角色词后面必须直接接状态或动作（如“值班人员休息”），"
    "中间不要插入时间词或其他修饰语；\n"
    "5. 每条事实必须附 evidence：来自转写片段时用该片段的 material_id 与 start_ms/end_ms，"
    "来自画面描述时用其 material_id 并以 timestamp_ms±1500 毫秒为区间；\n"
    "5a. 当画面描述通过门牌、标识牌或设备确认了具体场所（例如“消防控制室”），"
    "而转写内容在谈论该场所的人员值班、持证等情况时，必须把两者关联后规范表述："
    "用画面确认的场所名称（如“消防控制室”）替代转写中的口语说法（如“值班室”），"
    "例如“消防控制室值班人员当日休息、不在岗”，evidence 同时引用相关转写片段和画面；"
    "关联只限场所名称，不得新增人名、身份或结论；\n"
    '6. 只输出 JSON：{"facts": [{"content": "...", '
    '"evidence": [{"material_id": "...", "start_ms": 0, "end_ms": 1000}], '
    '"needs_verification": false, "verification_note": "", '
    '"is_final_enforcement_conclusion": false}]}。'
)


class GatewayConfig:
    def __init__(self) -> None:
        self.api_key = os.environ.get("DASHSCOPE_API_KEY", "").strip()
        self.base_url = os.environ.get(
            "DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
        ).rstrip("/")
        self.asr_window_sec = float(os.environ.get("ASR_WINDOW_SEC", "30"))
        self.asr_overlap_sec = float(os.environ.get("ASR_OVERLAP_SEC", "2"))
        embed_dim = os.environ.get("EMBED_DIM", "").strip()
        self.embed_dim = int(embed_dim) if embed_dim else None
        if not self.api_key:
            raise SystemExit("DASHSCOPE_API_KEY is required in the process environment")
        if self.asr_window_sec <= 0 or self.asr_overlap_sec < 0:
            raise SystemExit("ASR window/overlap configuration is invalid")
        if self.asr_overlap_sec >= self.asr_window_sec:
            raise SystemExit("ASR_OVERLAP_SEC must be smaller than ASR_WINDOW_SEC")


class UpstreamError(RuntimeError):
    pass


def _chat_completion(
    config: GatewayConfig,
    *,
    model: str,
    messages: list[dict[str, Any]],
    json_mode: bool = False,
    timeout: float = 120,
    temperature: float | None = None,
) -> str:
    body: dict[str, Any] = {"model": model, "messages": messages}
    if temperature is not None:
        body["temperature"] = temperature
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    try:
        response = httpx.post(
            f"{config.base_url}/chat/completions",
            json=body,
            headers={"Authorization": f"Bearer {config.api_key}"},
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
        return str(payload["choices"][0]["message"]["content"])
    except httpx.HTTPStatusError as exc:
        raise UpstreamError(f"chat completion failed with HTTP {exc.response.status_code}") from exc
    except (httpx.RequestError, KeyError, TypeError, ValueError) as exc:
        raise UpstreamError("chat completion request or response invalid") from exc


def _extract_json_object(text: str) -> dict[str, Any]:
    """Parse a JSON object from a model reply, tolerating markdown fences."""

    candidate = text.strip()
    if candidate.startswith("```"):
        lines = candidate.splitlines()
        candidate = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start < 0 or end <= start:
        raise UpstreamError("model reply did not contain a JSON object")
    try:
        parsed = json.loads(candidate[start : end + 1])
    except json.JSONDecodeError as exc:
        raise UpstreamError("model reply JSON could not be parsed") from exc
    if not isinstance(parsed, dict):
        raise UpstreamError("model reply JSON was not an object")
    return parsed


def _transcribe_window(config: GatewayConfig, *, model: str, wav_path: Path) -> str:
    audio_b64 = base64.b64encode(wav_path.read_bytes()).decode("ascii")
    # qwen3-asr-flash accepts a bare audio payload only; system or text
    # parts are rejected with HTTP 400 by the DashScope compatible endpoint.
    text = _chat_completion(
        config,
        model=model,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_audio",
                        "input_audio": {
                            "data": f"data:audio/wav;base64,{audio_b64}",
                            "format": "wav",
                        },
                    }
                ],
            },
        ],
    )
    return text.strip()


def _handle_asr(
    config: GatewayConfig, request: dict[str, Any], files: dict[str, bytes]
) -> dict[str, Any]:
    media = files.get("media")
    if not media:
        raise UpstreamError("missing media file")
    duration_ms = int(request["duration_ms"])
    model = str(request.get("model") or "").strip()
    if not model:
        raise UpstreamError("missing model")

    duration_sec = duration_ms / 1000.0
    window = config.asr_window_sec
    step = window - config.asr_overlap_sec
    windows: list[tuple[float, float]] = []
    start = 0.0
    while start < duration_sec:
        windows.append((start, min(start + window, duration_sec)))
        start += step
    if not windows:
        windows.append((0.0, duration_sec))

    with tempfile.TemporaryDirectory(prefix="dev-ai-gateway-asr-") as temporary:
        temp_dir = Path(temporary)
        source_path = temp_dir / "source.bin"
        source_path.write_bytes(media)
        wav_full = temp_dir / "full.wav"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-v",
                "error",
                "-i",
                str(source_path),
                "-ar",
                "16000",
                "-ac",
                "1",
                "-f",
                "wav",
                str(wav_full),
            ],
            check=True,
            capture_output=True,
            timeout=120,
        )
        chunk_paths: list[Path] = []
        for index, (window_start, window_end) in enumerate(windows):
            chunk = temp_dir / f"chunk-{index}.wav"
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-v",
                    "error",
                    "-ss",
                    f"{window_start:.3f}",
                    "-t",
                    f"{window_end - window_start:.3f}",
                    "-i",
                    str(wav_full),
                    "-f",
                    "wav",
                    str(chunk),
                ],
                check=True,
                capture_output=True,
                timeout=60,
            )
            chunk_paths.append(chunk)
        with ThreadPoolExecutor(max_workers=4) as pool:
            texts = list(
                pool.map(
                    lambda path: _transcribe_window(config, model=model, wav_path=path),
                    chunk_paths,
                )
            )

    segments: list[dict[str, Any]] = []
    full_text_parts: list[str] = []
    for (window_start, window_end), text in zip(windows, texts, strict=True):
        if not text:
            text = SILENCE_MARKER
        else:
            full_text_parts.append(text)
        segments.append(
            {
                "start_ms": int(window_start * 1000),
                "end_ms": int(window_end * 1000),
                "text": text,
                "suggested_role": "unknown",
            }
        )
    return {"segments": segments, "full_text": "\n".join(full_text_parts)}


def _describe_frame_batch(
    config: GatewayConfig,
    *,
    model: str,
    batch: list[tuple[int, int, bytes]],
) -> dict[int, str]:
    content: list[dict[str, Any]] = []
    for index, timestamp_ms, image_bytes in batch:
        content.append(
            {
                "type": "text",
                "text": f"帧序号 {index}，时间戳 {timestamp_ms} 毫秒：",
            }
        )
        content.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": "data:image/jpeg;base64," + base64.b64encode(image_bytes).decode("ascii")
                },
            }
        )
    content.append({"type": "text", "text": "逐帧输出 JSON 描述。"})
    reply = _chat_completion(
        config,
        model=model,
        messages=[
            {"role": "system", "content": _VISION_SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ],
        json_mode=True,
    )
    parsed = _extract_json_object(reply)
    frames = parsed.get("frames")
    if not isinstance(frames, list):
        raise UpstreamError("vision reply missing frames list")
    descriptions: dict[int, str] = {}
    for item in frames:
        if not isinstance(item, dict):
            continue
        try:
            index = int(item["index"])
            text = str(item["description"]).strip()
        except (KeyError, TypeError, ValueError):
            continue
        if text:
            descriptions[index] = text
    return descriptions


def _handle_vision(
    config: GatewayConfig, request: dict[str, Any], files: dict[str, bytes]
) -> dict[str, Any]:
    timestamps = [int(value) for value in request.get("timestamps_ms", [])]
    model = str(request.get("model") or "").strip()
    if not model:
        raise UpstreamError("missing model")
    frames = files.get("frames", [])
    if not isinstance(frames, list) or len(frames) != len(timestamps):
        raise UpstreamError("frames do not match timestamps")

    indexed = list(zip(range(len(timestamps)), timestamps, frames, strict=True))
    batches = [indexed[offset : offset + 4] for offset in range(0, len(indexed), 4)]
    descriptions: dict[int, str] = {}
    with ThreadPoolExecutor(max_workers=2) as pool:
        for partial in pool.map(
            lambda batch: _describe_frame_batch(config, model=model, batch=batch), batches
        ):
            descriptions.update(partial)

    results = []
    for index, timestamp_ms, _image in indexed:
        text = descriptions.get(index, "").strip()
        if not text:
            raise UpstreamError(f"vision reply missing frame {index}")
        results.append({"timestamp_ms": timestamp_ms, "text": text})
    return {"descriptions": results}


_LABELED_PERSON = re.compile(
    r"(?:姓名|名为|名叫|自称为|负责人姓名|值班人员姓名|被询问人|当事人)"
    r"[：:=为是\s]+([\u4e00-\u9fff·]{2,4}?)(?=[，。；、\s]|$)"
)
_ROLE_PERSON = (
    re.compile(
        r"([\u4e00-\u9fff·]{2,4}?)(?:为|是|系|担任)"
        r"(?:消防控制室)?(?:值班员|值班人员|负责人|工作人员|执法人员)"
    ),
    re.compile(
        r"(?:值班员|值班人员|负责人|当事人)"
        r"([\u4e00-\u9fff·]{2,4}?)(?=不在岗|在岗|休息|当班|值班|持证|未持证)"
    ),
)
_NON_NAMES = {"身份", "情况", "人员", "负责人", "值班员", "值班人员"}


def _normalized(value: str) -> str:
    import unicodedata

    normalized = unicodedata.normalize("NFKC", str(value)).casefold()
    return re.sub(r"[^\w\u4e00-\u9fff·-]+", "", normalized)


def _log_unsupported_person_names(untrusted: dict[str, Any], facts: list[Any]) -> None:
    """Log person-name tokens that the acceptance scanner would reject."""

    source_parts = [str(untrusted.get("full_text", ""))]
    source_parts.extend(str(item.get("text", "")) for item in untrusted.get("segments", []))
    normalized_sources = [_normalized(part) for part in source_parts]
    for fact in facts:
        if not isinstance(fact, dict):
            continue
        content = str(fact.get("content", ""))
        names = set(_LABELED_PERSON.findall(content))
        for pattern in _ROLE_PERSON:
            names.update(pattern.findall(content))
        names.difference_update(_NON_NAMES)
        for name in names:
            needle = _normalized(name)
            if needle and not any(needle in source for source in normalized_sources):
                logger.warning(
                    "fact extraction emitted a person-name token absent from source: %r",
                    name,
                )


def _handle_fact_extraction(config: GatewayConfig, request: dict[str, Any]) -> dict[str, Any]:
    model = str(request.get("model") or "").strip()
    if not model:
        raise UpstreamError("missing model")
    untrusted = request.get("untrusted_data")
    if not isinstance(untrusted, dict):
        raise UpstreamError("missing untrusted_data")
    reply = _chat_completion(
        config,
        model=model,
        messages=[
            {"role": "system", "content": _FACT_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(untrusted, ensure_ascii=False),
            },
        ],
        json_mode=True,
        timeout=180,
        temperature=0.2,
    )
    parsed = _extract_json_object(reply)
    facts = parsed.get("facts")
    if not isinstance(facts, list) or not facts:
        raise UpstreamError("fact extraction returned no facts")
    _log_unsupported_person_names(untrusted, facts)
    for item in facts:
        if isinstance(item, dict):
            item["is_final_enforcement_conclusion"] = False
    return {"facts": facts}


def _handle_embedding(config: GatewayConfig, request: dict[str, Any]) -> dict[str, Any]:
    model = str(request.get("model") or "").strip()
    untrusted = request.get("untrusted_data") or {}
    texts = untrusted.get("texts")
    if not model or not isinstance(texts, list) or not texts:
        raise UpstreamError("embedding request invalid")

    vectors: list[list[float]] = []
    for offset in range(0, len(texts), 10):
        batch = [str(text) for text in texts[offset : offset + 10]]
        body: dict[str, Any] = {"model": model, "input": batch}
        if config.embed_dim:
            body["dimensions"] = config.embed_dim
        try:
            response = httpx.post(
                f"{config.base_url}/embeddings",
                json=body,
                headers={"Authorization": f"Bearer {config.api_key}"},
                timeout=120,
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPStatusError as exc:
            raise UpstreamError(f"embedding failed with HTTP {exc.response.status_code}") from exc
        except httpx.RequestError as exc:
            raise UpstreamError("embedding request failed") from exc
        data = payload.get("data")
        if not isinstance(data, list) or len(data) != len(batch):
            raise UpstreamError("embedding count mismatch")
        ordered = sorted(data, key=lambda item: int(item.get("index", 0)))
        vectors.extend([list(item["embedding"]) for item in ordered])

    dimensions = len(vectors[0]) if vectors else 0
    fingerprint = f"{model}:{dimensions}"
    return {
        "embeddings": [{"values": values, "model_fingerprint": fingerprint} for values in vectors]
    }


class GatewayHandler(BaseHTTPRequestHandler):
    server_version = "FireInspectDevAIGateway/1.0"
    config: GatewayConfig  # set on the server instance before serving

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib name
        logger.info("gateway: " + format, *args)

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _parse_request(self) -> tuple[dict[str, Any], dict[str, Any]]:
        content_type = self.headers.get("Content-Type", "")
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        if content_type.startswith("multipart/form-data"):
            message = BytesParser(policy=email_policy).parsebytes(
                b"Content-Type: " + content_type.encode("ascii") + b"\r\n\r\n" + body
            )
            request: dict[str, Any] = {}
            files: dict[str, Any] = {}
            for part in message.iter_parts():
                name = part.get_param("name", header="content-disposition")
                if not name:
                    continue
                payload = part.get_payload(decode=True) or b""
                if part.get_filename():
                    if name in files:
                        existing = files[name]
                        if not isinstance(existing, list):
                            files[name] = [existing]
                        files[name].append(payload)
                    else:
                        files[name] = payload
                elif name == "request":
                    request = json.loads(payload.decode("utf-8"))
            return request, files
        return json.loads(body.decode("utf-8")), {}

    def do_POST(self) -> None:  # noqa: N802 - stdlib name
        started = time.monotonic()
        try:
            authorization = self.headers.get("Authorization", "")
            if not authorization.startswith("Bearer ") or not authorization[7:].strip():
                self._send_json(401, {"error": "missing bearer token"})
                return
            request, files = self._parse_request()
            capability = str(request.get("capability") or "")
            logger.info(
                "capability=%s files=%s",
                capability,
                sorted(files),
            )
            if capability == "asr":
                result = _handle_asr(self.server.gateway_config, request, files)  # type: ignore[attr-defined]
            elif capability == "vision":
                raw_frames = files.get("frames", [])
                if isinstance(raw_frames, bytes):
                    raw_frames = [raw_frames]
                result = _handle_vision(
                    self.server.gateway_config,
                    request,
                    {"frames": raw_frames},  # type: ignore[attr-defined]
                )
            elif capability == "fact_extraction":
                result = _handle_fact_extraction(self.server.gateway_config, request)  # type: ignore[attr-defined]
            elif capability == "embedding":
                result = _handle_embedding(self.server.gateway_config, request)  # type: ignore[attr-defined]
            else:
                self._send_json(
                    400, {"error": f"unsupported capability: {capability or 'missing'}"}
                )
                return
            self._send_json(200, result)
        except (UpstreamError, subprocess.CalledProcessError, ValueError, KeyError) as exc:
            logger.warning("request failed after %.1fs: %s", time.monotonic() - started, exc)
            self._send_json(502, {"error": str(exc)[:200]})
        except Exception:  # noqa: BLE001 - gateway must never leak internals
            logger.exception("unexpected gateway failure")
            self._send_json(500, {"error": "internal gateway error"})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = GatewayConfig()
    server = ThreadingHTTPServer((args.host, args.port), GatewayHandler)
    server.gateway_config = config  # type: ignore[attr-defined]
    logger.info("dev AI gateway listening on http://%s:%d", args.host, args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        threading.Thread(target=server.shutdown, daemon=True).start()
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
