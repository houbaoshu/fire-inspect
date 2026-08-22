"""AI 能力客户端：五组可配置端点（PLAN §1.2），httpx 直连 OpenAI 兼容协议。

调用形态与 ASR-VERIFICATION §1 一致：
- ASR：POST {base}/chat/completions，messages 带 input_audio(base64 data URI)，asr_options{language:"zh", enable_itn:true}
- LLM/VL：chat/completions（VL 用 image_url data URI）
- Embedding：POST {base}/embeddings
错误处理：已观测到 "HTTP 400 空错误体" 形态，统一解析为 AIError(reason)。
"""
import base64, json, re
import httpx
from .config import settings

AUDIO_MIME = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4", ".aac": "audio/aac"}
IMAGE_MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}


class AIError(Exception):
    """外部 AI 能力调用失败；message 面向用户展示（PRD §4 显示失败原因）。"""


def _client(timeout=180.0) -> httpx.Client:
    return httpx.Client(timeout=timeout)


def _check(resp: httpx.Response, group: str) -> dict:
    if resp.status_code != 200:
        body = (resp.text or "").strip()
        raise AIError(f"{group} 调用失败：HTTP {resp.status_code}" + (f" {body[:300]}" if body else "（空错误体）"))
    try:
        return resp.json()
    except Exception:
        raise AIError(f"{group} 返回非 JSON：{(resp.text or '')[:200]}")


def _data_uri(path: str, mimes: dict) -> str:
    ext = "." + path.rsplit(".", 1)[-1].lower()
    mime = mimes.get(ext, "application/octet-stream")
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    return f"data:{mime};base64,{b64}"


def asr_transcribe(audio_path: str) -> str:
    """整段/窗口音频 → 文本。qwen3-asr-flash 无时间戳（ASR-VERIFICATION §3），时间锚由切分位置确定。"""
    base, key, model = settings.resolve("asr")
    if not (base and key):
        raise AIError("ASR 端点未配置（ASR_BASE_URL / ASR_API_KEY）")
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": [
            {"type": "input_audio", "input_audio": {"data": _data_uri(audio_path, AUDIO_MIME)}},
        ]}],
        "asr_options": {"language": "zh", "enable_itn": True},
    }
    with _client() as c:
        d = _check(c.post(f"{base}/chat/completions", json=payload,
                          headers={"Authorization": f"Bearer {key}"}), "ASR")
    try:
        return d["choices"][0]["message"]["content"].strip()
    except Exception:
        raise AIError(f"ASR 返回结构异常：{json.dumps(d, ensure_ascii=False)[:300]}")


def llm_chat(messages: list[dict], json_mode: bool = False, group: str = "llm",
             max_tokens: int | None = None) -> str:
    base, key, model = settings.resolve(group)
    if not (base and key):
        raise AIError(f"{group.upper()} 端点未配置（{group.upper()}_BASE_URL / {group.upper()}_API_KEY）")
    payload: dict = {"model": model, "messages": messages, "temperature": 0.1}
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    if max_tokens:
        payload["max_tokens"] = max_tokens
    with _client() as c:
        d = _check(c.post(f"{base}/chat/completions", json=payload,
                          headers={"Authorization": f"Bearer {key}"}), group.upper())
    try:
        return d["choices"][0]["message"]["content"]
    except Exception:
        raise AIError(f"{group.upper()} 返回结构异常：{json.dumps(d, ensure_ascii=False)[:300]}")


def llm_json(system: str, user: str, group: str = "llm"):
    """LLM → 结构化 JSON；对 Markdown 包裹/前后废话做鲁棒提取。"""
    text = llm_chat([{"role": "system", "content": system},
                     {"role": "user", "content": user}], json_mode=True, group=group)
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1)
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"[\[{].*[\]}]", text, re.S)
        if m:
            return json.loads(m.group(0))
        raise AIError(f"LLM 未返回可解析 JSON：{text[:300]}")


def vl_describe(image_path: str, prompt: str) -> str:
    base, key, model = settings.resolve("vl")
    if not (base and key):
        raise AIError("VL 端点未配置（VL_BASE_URL / VL_API_KEY）")
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": _data_uri(image_path, IMAGE_MIME)}},
            {"type": "text", "text": prompt},
        ]}],
        "temperature": 0.1,
    }
    with _client() as c:
        d = _check(c.post(f"{base}/chat/completions", json=payload,
                          headers={"Authorization": f"Bearer {key}"}), "VL")
    try:
        return d["choices"][0]["message"]["content"].strip()
    except Exception:
        raise AIError(f"VL 返回结构异常：{json.dumps(d, ensure_ascii=False)[:300]}")


def embed_texts(texts: list[str]) -> list[list[float]]:
    base, key, model = settings.resolve("embed")
    if not (base and key):
        raise AIError("Embedding 端点未配置（EMBED_BASE_URL / EMBED_API_KEY）")
    out: list[list[float]] = []
    with _client() as c:
        for i in range(0, len(texts), 10):  # 分批，防单批过大
            batch = texts[i:i + 10]
            d = _check(c.post(f"{base}/embeddings", json={"model": model, "input": batch},
                              headers={"Authorization": f"Bearer {key}"}), "EMBED")
            for item in sorted(d["data"], key=lambda x: x["index"]):
                vec = item["embedding"]
                if len(vec) != settings.embed_dim:
                    raise AIError(f"Embedding 维度 {len(vec)} 与配置 EMBED_DIM={settings.embed_dim} 不一致")
                out.append(vec)
    return out
