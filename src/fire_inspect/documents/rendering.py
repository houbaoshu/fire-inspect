"""LibreOffice/Poppler render preflight with an explicit font-fidelity gate."""

from __future__ import annotations

import logging
import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import unicodedata
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - production and acceptance run on Unix.
    fcntl = None

from PIL import Image

from fire_inspect.templates import validate_docx

from .package import finalize_interview_page_count

logger = logging.getLogger(__name__)


class DocumentRenderError(RuntimeError):
    pass


REQUIRED_TEMPLATE_FONTS: dict[str, frozenset[str]] = {
    "record": frozenset({"FangSong", "KaiTi", "Microsoft YaHei UI", "SimSun"}),
    "photo": frozenset({"FangSong", "SimSun"}),
    "interview": frozenset({"FangSong", "FZXiaoBiaoSong-B05S", "Microsoft YaHei", "SimSun"}),
}

_LIBREOFFICE_ATTEMPTS = 3
_LIBREOFFICE_RETRY_DELAYS = (0.25, 0.75)
_LIBREOFFICE_THREAD_LOCK = threading.Lock()
_TRANSIENT_LIBREOFFICE_DIAGNOSTICS = (
    "already running",
    "another instance",
    "existing libreoffice",
    "failed to connect",
    "cannot connect",
    "connection refused",
    "lock file",
    "profile in use",
    "resource busy",
    "temporarily unavailable",
)


@dataclass(frozen=True, slots=True)
class FontAudit:
    required_fonts: tuple[str, ...]
    missing_fonts: tuple[str, ...]

    @property
    def fidelity_ready(self) -> bool:
        return not self.missing_fonts


@dataclass(frozen=True, slots=True)
class RenderReport:
    page_count: int
    page_images: tuple[Path, ...]
    pdf_path: Path
    font_audit: FontAudit
    automated_precheck_passed: bool
    manual_review_required: bool
    warnings: tuple[str, ...]


def _normalized_font_name(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip().casefold()


def discover_font_families(*, fc_list_binary: str | Path | None = None) -> set[str]:
    executable = str(fc_list_binary or shutil.which("fc-list") or "")
    if not executable:
        return set()
    try:
        completed = subprocess.run(
            [executable, "-f", "%{family}\n"],
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    families: set[str] = set()
    for line in completed.stdout.splitlines():
        for family in line.replace("\\-", "-").split(","):
            if family.strip():
                families.add(family.strip())
    return families


def audit_render_fonts(
    expected_type: str,
    *,
    available_fonts: set[str] | frozenset[str] | None = None,
) -> FontAudit:
    try:
        required = REQUIRED_TEMPLATE_FONTS[expected_type]
    except KeyError as exc:
        raise ValueError("未知文书类型") from exc
    available = available_fonts if available_fonts is not None else discover_font_families()
    normalized_available = {_normalized_font_name(font) for font in available}
    missing = tuple(
        sorted(font for font in required if _normalized_font_name(font) not in normalized_available)
    )
    return FontAudit(required_fonts=tuple(sorted(required)), missing_fonts=missing)


def _resolve_binary(
    explicit: str | Path | None,
    command: str,
    macos_candidate: str | None = None,
) -> str:
    if explicit is not None:
        path = Path(explicit)
        if not path.is_file():
            raise DocumentRenderError(f"未找到 {command} 文书渲染程序")
        return str(path)
    discovered = shutil.which(command)
    if discovered:
        return discovered
    if macos_candidate and Path(macos_candidate).is_file():
        return macos_candidate
    raise DocumentRenderError(f"未找到 {command} 文书渲染程序")


def _page_is_nearly_blank(path: Path) -> bool:
    with Image.open(path) as image:
        gray = image.convert("L")
        histogram = gray.histogram()
        dark_pixels = sum(histogram[:245])
        return dark_pixels / max(image.width * image.height, 1) < 0.0001


@contextmanager
def _serialized_libreoffice_conversion() -> Iterator[None]:
    """Serialize LibreOffice startup across worker threads and local processes."""

    with _LIBREOFFICE_THREAD_LOCK:
        if fcntl is None:
            yield
            return
        user_suffix = str(os.getuid()) if hasattr(os, "getuid") else "process"
        lock_path = Path(tempfile.gettempdir()) / f"fire-inspect-libreoffice-{user_suffix}.lock"
        with lock_path.open("a+b") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _libreoffice_failure_text(exc: subprocess.CalledProcessError) -> str:
    values = (exc.stdout, exc.stderr, exc.output)
    parts: list[str] = []
    for value in values:
        if isinstance(value, bytes):
            parts.append(value.decode("utf-8", errors="replace"))
        elif isinstance(value, str):
            parts.append(value)
    return "\n".join(parts).casefold()


def _is_transient_libreoffice_failure(exc: subprocess.CalledProcessError) -> bool:
    if exc.returncode in {-int(signal.SIGABRT), 128 + int(signal.SIGABRT)}:
        return True
    diagnostics = _libreoffice_failure_text(exc)
    return any(marker in diagnostics for marker in _TRANSIENT_LIBREOFFICE_DIAGNOSTICS)


def _retry_libreoffice(attempt_number: int, *, reason: str) -> None:
    logger.warning(
        "LibreOffice conversion hit a transient %s; retrying (%d/%d)",
        reason,
        attempt_number + 1,
        _LIBREOFFICE_ATTEMPTS,
    )
    time.sleep(_LIBREOFFICE_RETRY_DELAYS[attempt_number - 1])


def _convert_docx_with_libreoffice(
    *,
    soffice: str,
    input_path: Path,
    destination_pdf: Path,
    working_dir: Path,
    timeout_seconds: int,
) -> None:
    """Convert with fresh profiles and retry only recognized startup transients."""

    for attempt_number in range(1, _LIBREOFFICE_ATTEMPTS + 1):
        attempt_dir = working_dir / f"libreoffice-attempt-{attempt_number}"
        profile = attempt_dir / "profile"
        profile.mkdir(parents=True)
        command = [
            soffice,
            "--headless",
            "--nologo",
            "--nodefault",
            "--nofirststartwizard",
            f"-env:UserInstallation={profile.resolve().as_uri()}",
            "--convert-to",
            "pdf",
            "--outdir",
            str(attempt_dir),
            str(input_path),
        ]
        try:
            with _serialized_libreoffice_conversion():
                subprocess.run(
                    command,
                    check=True,
                    capture_output=True,
                    timeout=timeout_seconds,
                )
        except subprocess.CalledProcessError as exc:
            if attempt_number < _LIBREOFFICE_ATTEMPTS and _is_transient_libreoffice_failure(exc):
                _retry_libreoffice(attempt_number, reason="startup failure")
                continue
            raise

        generated_pdf = attempt_dir / f"{input_path.stem}.pdf"
        if generated_pdf.is_file() and generated_pdf.stat().st_size > 0:
            shutil.copyfile(generated_pdf, destination_pdf)
            return
        if attempt_number < _LIBREOFFICE_ATTEMPTS:
            _retry_libreoffice(attempt_number, reason="missing output")
            continue
        raise DocumentRenderError("LibreOffice 未生成有效 PDF")


def render_docx_pages(
    payload: bytes,
    *,
    output_dir: Path,
    expected_type: str,
    available_fonts: set[str] | frozenset[str] | None = None,
    allow_font_substitution: bool = False,
    soffice_binary: str | Path | None = None,
    pdftoppm_binary: str | Path | None = None,
    timeout_seconds: int = 120,
) -> RenderReport:
    """Render every page and report, without claiming manual visual approval."""

    validate_docx(payload, filename="generated.docx")
    font_audit = audit_render_fonts(expected_type, available_fonts=available_fonts)
    if not font_audit.fidelity_ready and not allow_font_substitution:
        raise DocumentRenderError(
            f"缺少模板指定字体：{'、'.join(font_audit.missing_fonts)}；不能判定视觉通过"
        )
    soffice = _resolve_binary(
        soffice_binary,
        "LibreOffice",
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    )
    pdftoppm = _resolve_binary(pdftoppm_binary, "pdftoppm")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise DocumentRenderError("渲染输出目录必须为空")
    output_dir.mkdir(parents=True, exist_ok=True)

    warnings: list[str] = []
    if not font_audit.fidelity_ready:
        warnings.append("缺少模板指定字体，本次仅为结构和几何预览，不能作为视觉验收")
    try:
        with tempfile.TemporaryDirectory(prefix="fire-inspect-docx-render-") as temporary:
            temp_dir = Path(temporary)
            input_path = temp_dir / "document.docx"
            pdf_path = temp_dir / "document.pdf"
            input_path.write_bytes(payload)
            _convert_docx_with_libreoffice(
                soffice=soffice,
                input_path=input_path,
                destination_pdf=pdf_path,
                working_dir=temp_dir,
                timeout_seconds=timeout_seconds,
            )
            final_pdf = output_dir / "document.pdf"
            shutil.copyfile(pdf_path, final_pdf)
            subprocess.run(
                [pdftoppm, "-png", "-r", "150", str(pdf_path), str(output_dir / "page")],
                check=True,
                capture_output=True,
                timeout=timeout_seconds,
            )
    except DocumentRenderError:
        raise
    except subprocess.TimeoutExpired as exc:
        raise DocumentRenderError("文书渲染超时") from exc
    except (OSError, subprocess.CalledProcessError) as exc:
        raise DocumentRenderError("文书渲染失败，请检查 LibreOffice 和 Poppler") from exc

    page_images = tuple(
        sorted(
            output_dir.glob("page-*.png"),
            key=lambda path: int(path.stem.rsplit("-", 1)[-1]),
        )
    )
    if not page_images:
        raise DocumentRenderError("文书渲染没有生成逐页图片")
    blank_pages = [
        index for index, path in enumerate(page_images, start=1) if _page_is_nearly_blank(path)
    ]
    if blank_pages:
        raise DocumentRenderError("文书渲染出现异常空白页")
    return RenderReport(
        page_count=len(page_images),
        page_images=page_images,
        pdf_path=output_dir / "document.pdf",
        font_audit=font_audit,
        automated_precheck_passed=font_audit.fidelity_ready,
        manual_review_required=True,
        warnings=tuple(warnings),
    )


def finalize_interview_with_render(
    payload: bytes,
    *,
    output_dir: Path,
    available_fonts: set[str] | frozenset[str] | None = None,
    allow_font_substitution: bool = False,
    soffice_binary: str | Path | None = None,
    pdftoppm_binary: str | Path | None = None,
) -> tuple[bytes, RenderReport]:
    """Render, correct NUMPAGES caches, then rerender the final interview DOCX."""

    initial = render_docx_pages(
        payload,
        output_dir=output_dir / "initial",
        expected_type="interview",
        available_fonts=available_fonts,
        allow_font_substitution=allow_font_substitution,
        soffice_binary=soffice_binary,
        pdftoppm_binary=pdftoppm_binary,
    )
    finalized = finalize_interview_page_count(payload, page_count=initial.page_count)
    final_report = render_docx_pages(
        finalized,
        output_dir=output_dir / "final",
        expected_type="interview",
        available_fonts=available_fonts,
        allow_font_substitution=allow_font_substitution,
        soffice_binary=soffice_binary,
        pdftoppm_binary=pdftoppm_binary,
    )
    if final_report.page_count != initial.page_count:
        raise DocumentRenderError("校正页码缓存后文书页数发生变化")
    return finalized, final_report
