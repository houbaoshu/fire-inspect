from __future__ import annotations

import json
import math
import os
from collections.abc import Iterable, Sequence
from hashlib import sha256

from fire_inspect.ai.providers import ProviderFailure
from fire_inspect.ai.schemas import EmbeddingOutput


def clause_embedding_text(document, clause) -> str:
    """Build the stable, provider-neutral text represented by one clause vector."""

    return "\n".join(
        part.strip()
        for part in (
            str(document.title),
            str(document.category),
            str(clause.chapter),
            str(clause.section),
            str(clause.clause_identifier),
            str(clause.heading),
            str(clause.content),
        )
        if part and part.strip()
    )


def embedding_configuration_fingerprint() -> str:
    """Fingerprint non-secret settings that can change vector compatibility."""

    prefix = "FIRE_INSPECT_EMBEDDING"
    payload = {
        "provider": os.environ.get(f"{prefix}_PROVIDER", "disabled").strip().lower(),
        "endpoint": os.environ.get(f"{prefix}_ENDPOINT", "").strip(),
        "model": os.environ.get(f"{prefix}_MODEL", "").strip(),
    }
    return sha256(json.dumps(payload, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def embedding_input_fingerprint(document, clauses: Iterable[object]) -> str:
    payload = {
        "document_id": str(document.id),
        "document_revision": int(document.revision),
        "configuration": embedding_configuration_fingerprint(),
        "clauses": [
            {
                "id": str(clause.id),
                "sequence": int(clause.sequence),
                "text_sha256": sha256(clause_embedding_text(document, clause).encode()).hexdigest(),
            }
            for clause in clauses
        ],
    }
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def validate_embedding_batch(
    outputs: Sequence[EmbeddingOutput] | Iterable[EmbeddingOutput],
    *,
    expected_count: int,
) -> tuple[EmbeddingOutput, ...]:
    try:
        normalized = tuple(outputs)
    except TypeError as exc:
        raise ProviderFailure("Embedding 服务返回格式无效，未保存本次结果") from exc
    if len(normalized) != expected_count:
        raise ProviderFailure("Embedding 返回数量与输入不一致，未保存本次结果")
    if not normalized:
        raise ProviderFailure("Embedding 未返回可用结果，未保存本次结果")

    fingerprints: set[str] = set()
    dimensions: set[int] = set()
    for item in normalized:
        try:
            values = tuple(item.values)
            raw_fingerprint = item.model_fingerprint
        except (AttributeError, TypeError) as exc:
            raise ProviderFailure("Embedding 服务返回格式无效，未保存本次结果") from exc
        if not isinstance(raw_fingerprint, str):
            raise ProviderFailure("Embedding 模型指纹无效，未保存本次结果")
        fingerprint = raw_fingerprint.strip()
        if not values or not fingerprint or len(fingerprint) > 200:
            raise ProviderFailure("Embedding 结果为空，未保存本次结果")
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            for value in values
        ):
            raise ProviderFailure("Embedding 包含非法数值，未保存本次结果")
        if not any(float(value) != 0 for value in values):
            raise ProviderFailure("Embedding 返回零向量，未保存本次结果")
        fingerprints.add(fingerprint)
        dimensions.add(len(values))
    if len(fingerprints) != 1:
        raise ProviderFailure("Embedding 同批结果模型指纹不一致，未保存本次结果")
    if len(dimensions) != 1 or next(iter(dimensions)) <= 0:
        raise ProviderFailure("Embedding 同批结果维度不一致，未保存本次结果")
    return normalized


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if not left or len(left) != len(right):
        raise ValueError("向量维度不一致")
    numerator = sum(float(a) * float(b) for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(float(value) ** 2 for value in left))
    right_norm = math.sqrt(sum(float(value) ** 2 for value in right))
    if left_norm == 0 or right_norm == 0:
        raise ValueError("零向量不能参与余弦检索")
    score = numerator / (left_norm * right_norm)
    if not math.isfinite(score):
        raise ValueError("向量相似度无效")
    return max(-1.0, min(1.0, score))
