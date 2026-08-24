from pathlib import Path

import pytest

from fire_inspect.acceptance import (
    AssetIntegrityError,
    load_acceptance_cases,
    verify_authoritative_assets,
)

ROOT = Path(__file__).resolve().parents[2]


def test_non_sensitive_authoritative_assets_match_acceptance_hashes() -> None:
    verified = verify_authoritative_assets(ROOT)
    assert len(verified) == 4


@pytest.mark.local_acceptance
def test_sensitive_video_assets_match_acceptance_hashes() -> None:
    missing = [
        path
        for path in (ROOT / "testdata/videos/1.mp4", ROOT / "testdata/videos/4.mp4")
        if not path.is_file()
    ]
    if missing:
        pytest.skip("authorized local acceptance videos are unavailable")
    verified = verify_authoritative_assets(ROOT, include_sensitive=True)
    assert len(verified) == 6


def test_acceptance_case_schema_is_nonempty() -> None:
    payload = load_acceptance_cases(ROOT)
    assert {case["id"] for case in payload["cases"]} == {
        "video-1-control-room-duty",
        "video-4-storage-use-change",
    }


def test_hash_mismatch_never_displays_file_contents(tmp_path: Path) -> None:
    (tmp_path / "docs/templates").mkdir(parents=True)
    (tmp_path / "testdata/knowledge").mkdir(parents=True)
    for relative in (
        "docs/templates/照片.docx",
        "docs/templates/询问笔录.docx",
        "docs/templates/监督检查记录表.docx",
        "testdata/knowledge/reg_ec997c582c9c29a6.pdf",
    ):
        (tmp_path / relative).write_bytes(b"SECRET-CONTENT-CANARY")
    with pytest.raises(AssetIntegrityError) as error:
        verify_authoritative_assets(tmp_path)
    assert "SECRET-CONTENT-CANARY" not in str(error.value)
