"""Authoritative acceptance inputs and integrity checks.

This module deliberately uses only the standard library so it can run before
the application, database, or optional AI providers are configured.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path


@dataclass(frozen=True, slots=True)
class AuthoritativeAsset:
    relative_path: str
    sha256: str
    sensitive: bool = False


AUTHORITATIVE_ASSETS: tuple[AuthoritativeAsset, ...] = (
    AuthoritativeAsset(
        "docs/templates/照片.docx",
        "6199a905ebd4a0453c90982f5a23987ea54e15ef0c1e8e004b1e50be3d13cdac",
    ),
    AuthoritativeAsset(
        "docs/templates/询问笔录.docx",
        "e15b011e14e0429d6ffc90a22dddf9682d402814b0d6e11b01344cceb53a0819",
    ),
    AuthoritativeAsset(
        "docs/templates/监督检查记录表.docx",
        "37726202a5ae32b96230504388611c9cc824b3cc1abb798b52efd091f72baae5",
    ),
    AuthoritativeAsset(
        "testdata/knowledge/reg_ec997c582c9c29a6.pdf",
        "92580dffac57f26f18955271332f819841ba4ad968964626d93146f3094b2576",
    ),
    AuthoritativeAsset(
        "testdata/videos/1.mp4",
        "2f294e5069511646f7fa41576b17b30b47f0d4012326eca554a907a1e970a7e5",
        sensitive=True,
    ),
    AuthoritativeAsset(
        "testdata/videos/4.mp4",
        "9e0a71029a5a1ed07c8a89f4f2274f989a0c9d8f03ad117a3701653489829195",
        sensitive=True,
    ),
)


class AssetIntegrityError(RuntimeError):
    """Raised when an authoritative input is missing or has changed."""


def file_sha256(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def selected_assets(*, include_sensitive: bool) -> Iterable[AuthoritativeAsset]:
    return (asset for asset in AUTHORITATIVE_ASSETS if include_sensitive or not asset.sensitive)


def verify_authoritative_assets(
    repository_root: Path, *, include_sensitive: bool = False
) -> dict[str, str]:
    """Return verified hashes, raising without leaking file contents."""
    verified: dict[str, str] = {}
    failures: list[str] = []
    for asset in selected_assets(include_sensitive=include_sensitive):
        path = repository_root / asset.relative_path
        if not path.is_file():
            failures.append(f"{asset.relative_path}: missing")
            continue
        actual = file_sha256(path)
        if actual != asset.sha256:
            failures.append(f"{asset.relative_path}: sha256 mismatch")
            continue
        verified[asset.relative_path] = actual
    if failures:
        raise AssetIntegrityError("; ".join(failures))
    return verified


def load_acceptance_cases(repository_root: Path) -> dict[str, object]:
    path = repository_root / "testdata/expected/acceptance-cases.json"
    with path.open("r", encoding="utf-8") as stream:
        payload = json.load(stream)
    if payload.get("schema_version") != 1:
        raise AssetIntegrityError("unsupported acceptance-cases schema")
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise AssetIntegrityError("acceptance-cases must contain cases")
    return payload
