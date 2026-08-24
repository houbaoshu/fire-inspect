from __future__ import annotations

import signal
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pytest
from lxml import etree
from pypdf import PdfReader

from fire_inspect.documents import (
    DocumentRenderError,
    FontAudit,
    InterviewDocumentData,
    QaItem,
    QaRole,
    RenderReport,
    audit_render_fonts,
    generate_interview_docx,
    render_docx_pages,
    rendering,
)
from fire_inspect.documents.services import render_generated_document

NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}


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


def _footer_field_state(payload: bytes) -> tuple[tuple[str, ...], tuple[str, ...]]:
    page_instructions: list[str] = []
    cached_totals: list[str] = []
    with ZipFile(BytesIO(payload)) as archive:
        for name in sorted(
            item
            for item in archive.namelist()
            if item.startswith("word/footer") and item.endswith(".xml")
        ):
            root = etree.fromstring(archive.read(name))
            page_instructions.extend(root.xpath(".//w:instrText/text()", namespaces=NS))
            cached_totals.extend(
                root.xpath(
                    './/w:fldSimple[contains(@w:instr, "NUMPAGES")]//w:t/text()',
                    namespaces=NS,
                )
            )
    return tuple(page_instructions), tuple(cached_totals)


def test_font_audit_requires_the_template_fonts_not_generic_substitutes() -> None:
    audit = audit_render_fonts(
        "interview",
        available_fonts={"FZXiaoBiaoSong-B05S", "STFangsong", "Songti SC"},
    )

    assert "FangSong" in audit.missing_fonts
    assert "SimSun" in audit.missing_fonts
    assert not audit.fidelity_ready


def test_render_refuses_to_claim_success_when_required_chinese_fonts_are_missing(
    template_dir: Path, tmp_path: Path
) -> None:
    with pytest.raises(DocumentRenderError, match="缺少模板指定字体"):
        render_docx_pages(
            (template_dir / "询问笔录.docx").read_bytes(),
            output_dir=tmp_path / "render",
            expected_type="interview",
            available_fonts=set(),
        )
    assert not (tmp_path / "render").exists()


def test_font_substitution_mode_is_explicitly_preview_only(
    template_dir: Path, tmp_path: Path
) -> None:
    # A missing renderer should be reported only after the caller explicitly
    # accepts that missing fonts make this a geometry preview, not visual QA.
    with pytest.raises(DocumentRenderError, match="LibreOffice"):
        render_docx_pages(
            (template_dir / "照片.docx").read_bytes(),
            output_dir=tmp_path / "render",
            expected_type="photo",
            available_fonts=set(),
            allow_font_substitution=True,
            soffice_binary=tmp_path / "missing-soffice",
        )


def _converter_input(tmp_path: Path, name: str = "document") -> tuple[Path, Path, Path]:
    working_dir = tmp_path / name
    working_dir.mkdir()
    input_path = working_dir / "document.docx"
    input_path.write_bytes(b"validated upstream")
    return input_path, working_dir / "document.pdf", working_dir


def test_libreoffice_sigabrt_retries_with_a_fresh_profile_and_backoff(
    monkeypatch,
    tmp_path: Path,
) -> None:
    input_path, destination_pdf, working_dir = _converter_input(tmp_path)
    commands: list[list[str]] = []
    delays: list[float] = []

    def run(command, **_kwargs):
        commands.append(command)
        if len(commands) == 1:
            raise subprocess.CalledProcessError(-int(signal.SIGABRT), command)
        output_dir = Path(command[command.index("--outdir") + 1])
        (output_dir / "document.pdf").write_bytes(b"%PDF-recovered")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(rendering.subprocess, "run", run)
    monkeypatch.setattr(rendering.time, "sleep", delays.append)

    rendering._convert_docx_with_libreoffice(
        soffice="soffice",
        input_path=input_path,
        destination_pdf=destination_pdf,
        working_dir=working_dir,
        timeout_seconds=10,
    )

    assert destination_pdf.read_bytes() == b"%PDF-recovered"
    assert len(commands) == 2
    profiles = [
        next(value for value in command if value.startswith("-env:")) for command in commands
    ]
    assert profiles[0] != profiles[1]
    assert delays == [rendering._LIBREOFFICE_RETRY_DELAYS[0]]


def test_libreoffice_success_without_output_gets_only_finite_retries(
    monkeypatch,
    tmp_path: Path,
) -> None:
    input_path, destination_pdf, working_dir = _converter_input(tmp_path)
    calls = 0
    delays: list[float] = []

    def run(command, **_kwargs):
        nonlocal calls
        calls += 1
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(rendering.subprocess, "run", run)
    monkeypatch.setattr(rendering.time, "sleep", delays.append)

    with pytest.raises(DocumentRenderError, match="未生成有效 PDF"):
        rendering._convert_docx_with_libreoffice(
            soffice="soffice",
            input_path=input_path,
            destination_pdf=destination_pdf,
            working_dir=working_dir,
            timeout_seconds=10,
        )

    assert calls == rendering._LIBREOFFICE_ATTEMPTS
    assert delays == list(rendering._LIBREOFFICE_RETRY_DELAYS)


@pytest.mark.parametrize("failure", ("invalid-input", "timeout"))
def test_libreoffice_non_transient_failure_and_timeout_are_not_retried(
    monkeypatch,
    tmp_path: Path,
    failure: str,
) -> None:
    input_path, destination_pdf, working_dir = _converter_input(tmp_path)
    calls = 0
    delays: list[float] = []

    def run(command, **_kwargs):
        nonlocal calls
        calls += 1
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, 10)
        raise subprocess.CalledProcessError(
            1,
            command,
            stderr=b"Error: source file could not be loaded",
        )

    monkeypatch.setattr(rendering.subprocess, "run", run)
    monkeypatch.setattr(rendering.time, "sleep", delays.append)

    expected = subprocess.TimeoutExpired if failure == "timeout" else subprocess.CalledProcessError
    with pytest.raises(expected):
        rendering._convert_docx_with_libreoffice(
            soffice="soffice",
            input_path=input_path,
            destination_pdf=destination_pdf,
            working_dir=working_dir,
            timeout_seconds=10,
        )

    assert calls == 1
    assert delays == []


def test_libreoffice_conversions_are_serialized_within_a_worker_process(
    monkeypatch,
    tmp_path: Path,
) -> None:
    state_lock = threading.Lock()
    start_barrier = threading.Barrier(2)
    active = 0
    maximum_active = 0

    def run(command, **_kwargs):
        nonlocal active, maximum_active
        with state_lock:
            active += 1
            maximum_active = max(maximum_active, active)
        try:
            time.sleep(0.05)
            output_dir = Path(command[command.index("--outdir") + 1])
            (output_dir / "document.pdf").write_bytes(b"%PDF-serialized")
            return subprocess.CompletedProcess(command, 0)
        finally:
            with state_lock:
                active -= 1

    monkeypatch.setattr(rendering.subprocess, "run", run)

    def convert(index: int) -> None:
        input_path, destination_pdf, working_dir = _converter_input(tmp_path, f"job-{index}")
        start_barrier.wait(timeout=2)
        rendering._convert_docx_with_libreoffice(
            soffice="soffice",
            input_path=input_path,
            destination_pdf=destination_pdf,
            working_dir=working_dir,
            timeout_seconds=10,
        )
        assert destination_pdf.is_file()

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(convert, (1, 2)))

    assert maximum_active == 1


@pytest.mark.parametrize(("long", "rendered_pages"), ((False, 3), (True, 6)))
def test_production_render_finalizes_interview_page_count_then_renders_final_payload(
    monkeypatch,
    template_dir: Path,
    tmp_path: Path,
    long: bool,
    rendered_pages: int,
) -> None:
    """Exercise the production service path without replacing its finalizer."""

    payload = generate_interview_docx(template_dir / "询问笔录.docx", _interview_data(long=long))
    observed_totals: list[tuple[str, ...]] = []

    def deterministic_renderer(
        candidate: bytes,
        *,
        output_dir: Path,
        expected_type: str,
        **_kwargs,
    ) -> RenderReport:
        del output_dir
        assert expected_type == "interview"
        page_instructions, totals = _footer_field_state(candidate)
        assert any("PAGE" in instruction.upper() for instruction in page_instructions)
        observed_totals.append(totals)
        return RenderReport(
            page_count=rendered_pages,
            page_images=tuple(tmp_path / f"page-{index}.png" for index in range(rendered_pages)),
            pdf_path=tmp_path / "document.pdf",
            font_audit=FontAudit(required_fonts=(), missing_fonts=()),
            automated_precheck_passed=True,
            manual_review_required=True,
            warnings=(),
        )

    monkeypatch.setattr(
        "fire_inspect.documents.rendering.render_docx_pages", deterministic_renderer
    )

    rendered = render_generated_document(payload, document_type="interview")

    assert rendered.page_count == rendered_pages
    assert rendered.payload != payload
    assert observed_totals == [("1", "1", "1"), (str(rendered_pages),) * 3]
    page_instructions, cached_totals = _footer_field_state(rendered.payload)
    assert any("PAGE" in instruction.upper() for instruction in page_instructions)
    assert cached_totals == (str(rendered_pages),) * 3


@pytest.mark.local_acceptance
@pytest.mark.parametrize("long", (False, True), ids=("under-four-pages", "over-four-pages"))
def test_real_rendered_interview_has_correct_current_and_total_page_numbers(
    template_dir: Path,
    tmp_path: Path,
    long: bool,
) -> None:
    """Use real LibreOffice twice in production, then inspect every final PDF page."""

    payload = generate_interview_docx(template_dir / "询问笔录.docx", _interview_data(long=long))
    rendered = render_generated_document(
        payload,
        document_type="interview",
        allow_font_substitution=True,
    )

    final_report = render_docx_pages(
        rendered.payload,
        output_dir=tmp_path / "final-evidence",
        expected_type="interview",
        allow_font_substitution=True,
    )
    assert final_report.page_count == rendered.page_count
    if long:
        assert rendered.page_count > 4
    else:
        assert rendered.page_count < 4

    pages = PdfReader(final_report.pdf_path).pages
    assert len(pages) == rendered.page_count
    for page_number, page in enumerate(pages, start=1):
        compact_text = "".join((page.extract_text() or "").split())
        assert f"第{page_number}页共{rendered.page_count}页" in compact_text

    _, cached_totals = _footer_field_state(rendered.payload)
    assert cached_totals == (str(rendered.page_count),) * 3
    assert rendered.render_check_result == {
        "passed": False,
        "font_fidelity": False,
        "manual_review_required": True,
        "warnings": ["缺少模板指定字体，本次仅为结构和几何预览，不能作为视觉验收"],
    }
