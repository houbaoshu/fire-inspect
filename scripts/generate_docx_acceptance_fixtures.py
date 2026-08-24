#!/usr/bin/env python3
"""Generate deterministic, non-sensitive DOCX files for Windows visual review."""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime
from hashlib import sha256
from io import BytesIO
from pathlib import Path

from PIL import Image

from fire_inspect.documents import (
    InspectionType,
    InterviewDocumentData,
    PhotoDocumentData,
    PhotoItem,
    PhotoSharedFields,
    QaItem,
    QaRole,
    RecordDocumentData,
    generate_interview_docx,
    generate_photo_docx,
    generate_record_docx,
)

ROOT = Path(__file__).resolve().parents[1]


def _image(size: tuple[int, int], color: tuple[int, int, int]) -> bytes:
    stream = BytesIO()
    Image.new("RGB", size, color).save(stream, format="PNG")
    return stream.getvalue()


def _interview_data(*, long: bool) -> InterviewDocumentData:
    rounds = 180 if long else 2
    qa_items = tuple(
        item
        for index in range(rounds)
        for item in (
            QaItem(QaRole.QUESTION, f"第{index + 1}个问题：请说明现场情况。"),
            QaItem(QaRole.ANSWER, "现场有物品堆放，区域用途情况需要进一步核实。"),
        )
    )
    return InterviewDocumentData(
        interview_number=2,
        started_at=datetime(2026, 8, 24, 23, 55),
        ended_at=datetime(2026, 8, 25, 0, 20),
        location="某市消防救援大队询问室",
        interviewer_name="检查员甲",
        interviewer_unit="某市消防救援大队",
        recorder_name="记录员乙",
        recorder_unit="某市消防救援大队",
        interviewee_name="王某",
        gender="男",
        age="42",
        birth_date="1984年3月",
        id_type="居民身份证",
        id_number="330000********0018",
        registered_address="浙江省某市某区某街道某社区",
        current_address="浙江省某市某区某路某号某幢某室" + ("很长地址" * 30 if long else ""),
        contact="138****0000",
        qa_items=qa_items,
    )


def generate_review_set(output_dir: Path) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    images = (
        _image((800, 450), (210, 235, 250)),
        _image((450, 800), (235, 225, 210)),
        _image((640, 640), (220, 240, 220)),
    )
    payloads = {
        "record.docx": generate_record_docx(
            ROOT / "docs/templates/监督检查记录表.docx",
            RecordDocumentData(
                document_number="桐消监记〔2026〕18号",
                inspection_date=date(2026, 8, 24),
                inspection_type=InspectionType.CONSTRUCTION_SITE,
                unit_name="某某商业管理有限公司",
                responsible_person="张某",
                address="浙江省某市某区某路88号",
                contact_name="李某",
                contact_phone="138****0000",
                inspection_details="大厅存在堆放物品，相关区域用途需要进一步核实。",
                remarks="仅记录现场情况，不作自动执法结论。",
            ),
        ),
        "photo-three-pages.docx": generate_photo_docx(
            ROOT / "docs/templates/照片.docx",
            PhotoDocumentData(
                items=tuple(
                    PhotoItem(image, f"第{index}张照片反映内容", f"第{index}张照片说明")
                    for index, image in enumerate(images, start=1)
                ),
                shared=PhotoSharedFields(
                    shooting_location="某单位一层大厅",
                    photographer="检查员甲",
                    shooting_time=datetime(2026, 8, 24, 10, 30),
                ),
            ),
        ),
        "interview-short.docx": generate_interview_docx(
            ROOT / "docs/templates/询问笔录.docx", _interview_data(long=False)
        ),
        "interview-long.docx": generate_interview_docx(
            ROOT / "docs/templates/询问笔录.docx", _interview_data(long=True)
        ),
    }
    hashes: dict[str, str] = {}
    for name, payload in payloads.items():
        path = output_dir / name
        path.write_bytes(payload)
        hashes[name] = sha256(payload).hexdigest()
    (output_dir / "manifest.generated.json").write_text(
        json.dumps(
            {"schema_version": 1, "documents": hashes},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return hashes


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    arguments = parser.parse_args()
    hashes = generate_review_set(arguments.output_dir.resolve())
    print(f"generated {len(hashes)} DOCX review fixtures")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
