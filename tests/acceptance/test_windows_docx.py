from __future__ import annotations

import json
import os
from hashlib import sha256
from pathlib import Path

import pytest
from PIL import Image
from scripts.generate_docx_acceptance_fixtures import generate_review_set

from fire_inspect.acceptance import file_sha256

ROOT = Path(__file__).resolve().parents[2]
REQUIRED_FONTS = {
    "FangSong",
    "FZXiaoBiaoSong-B05S",
    "KaiTi",
    "Microsoft YaHei",
    "Microsoft YaHei UI",
    "SimSun",
}


def _visual_baseline_sha256(pages: list[dict[str, object]]) -> str:
    manifest = "".join(f"{int(page['page_number'])}:{str(page['sha256'])}\n" for page in pages)
    return sha256(manifest.encode("ascii")).hexdigest()


def _verify_rendered_page(output_dir: Path, page: dict[str, object]) -> None:
    relative = Path(str(page.get("image", "")))
    assert relative and not relative.is_absolute() and ".." not in relative.parts
    image_path = (output_dir / relative).resolve()
    assert image_path.is_relative_to(output_dir) and image_path.is_file()
    assert image_path.suffix.casefold() == ".png"
    assert file_sha256(image_path) == page.get("sha256")
    with Image.open(image_path) as rendered:
        rendered.verify()
    with Image.open(image_path) as rendered:
        assert rendered.width >= 500 and rendered.height >= 500


@pytest.mark.windows_docx
def test_designated_windows_word_or_wps_review_approves_every_generated_page() -> None:
    output_dir = Path(os.environ["FIRE_INSPECT_WINDOWS_DOCX_OUTPUT_DIR"]).resolve()
    approval_path = Path(os.environ["FIRE_INSPECT_WINDOWS_DOCX_APPROVAL_FILE"]).resolve()
    generated_hashes = generate_review_set(output_dir)

    assert approval_path.is_file(), (
        "先在指定 Windows + Word/WPS + 字体环境打开输出目录中的四份 DOCX，"
        "更新全部域并逐页检查，再提供审批 JSON"
    )
    approval = json.loads(approval_path.read_text(encoding="utf-8"))
    assert approval.get("schema_version") == 2
    environment = approval.get("environment", {})
    assert environment.get("os") == "Windows"
    assert environment.get("application") in {"Microsoft Word", "WPS Writer"}
    assert str(environment.get("version", "")).strip()
    assert REQUIRED_FONTS.issubset(set(environment.get("fonts", [])))
    assert str(environment.get("render_export_method", "")).strip()
    assert approval.get("fields_updated") is True
    assert approval.get("all_pages_checked_at_100_percent") is True
    assert str(approval.get("reviewer", "")).strip()
    assert str(approval.get("reviewed_at", "")).strip()

    documents = approval.get("documents", {})
    assert set(documents) == set(generated_hashes)
    for name, expected_hash in generated_hashes.items():
        result = documents[name]
        assert result.get("source_docx_sha256") == expected_hash
        assert result.get("passed") is True
        page_count = int(result.get("page_count", 0))
        assert page_count > 0
        assert result.get("no_clipping_or_overlap") is True
        assert result.get("fonts_images_footer_valid") is True
        pages = result.get("rendered_pages")
        assert isinstance(pages, list)
        assert [int(page["page_number"]) for page in pages] == list(range(1, page_count + 1))
        assert all(page.get("source_docx_sha256") == expected_hash for page in pages)
        assert result.get("visual_baseline_sha256") == _visual_baseline_sha256(pages)
        for page in pages:
            _verify_rendered_page(output_dir, page)
        if name.startswith("interview-"):
            assert all(
                int(page.get("footer_current_page", 0)) == int(page["page_number"])
                and int(page.get("footer_total_pages", 0)) == page_count
                for page in pages
            )
    assert int(documents["photo-three-pages.docx"]["page_count"]) == 3
    assert int(documents["interview-short.docx"]["page_count"]) < 4
    assert int(documents["interview-long.docx"]["page_count"]) > 4
