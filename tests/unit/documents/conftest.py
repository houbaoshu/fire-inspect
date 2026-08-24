from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def template_dir() -> Path:
    return ROOT / "docs/templates"


def _image_bytes(size: tuple[int, int], color: tuple[int, int, int], label: str) -> bytes:
    image = Image.new("RGB", size, color)
    draw = ImageDraw.Draw(image)
    draw.rectangle((4, 4, size[0] - 5, size[1] - 5), outline="black", width=3)
    draw.text((12, 12), label, fill="black")
    stream = BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()


@pytest.fixture
def three_test_images() -> tuple[bytes, bytes, bytes]:
    return (
        _image_bytes((800, 450), (210, 235, 250), "wide"),
        _image_bytes((450, 800), (235, 225, 210), "tall"),
        _image_bytes((640, 640), (220, 240, 220), "square"),
    )
