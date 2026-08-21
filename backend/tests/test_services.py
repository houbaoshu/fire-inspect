import io

import pytest
from app.services.documents import scan_template_fields
from app.services.knowledge import extract_document, split_chunks
from docx import Document


def test_template_placeholder_scan():
    document = Document()
    document.add_paragraph("单位：{{ organization }}")
    document.add_paragraph("事实：{{ facts }}")
    output = io.BytesIO()
    document.save(output)
    assert scan_template_fields(output.getvalue()) == ["facts", "organization"]


def test_template_rejects_non_field_jinja_expressions():
    document = Document()
    document.add_paragraph("{{ organization }}")
    document.add_paragraph("{{ unsafe.attribute }}")
    output = io.BytesIO()
    document.save(output)
    with pytest.raises(ValueError, match="只能使用"):
        scan_template_fields(output.getvalue())


def test_text_knowledge_extraction_and_chunking():
    sections = extract_document("第一条 消防安全要求\n第二条 值班要求".encode(), "rules.txt")
    chunks = split_chunks(sections, max_chars=20)
    assert chunks
    assert all(locator == "全文" for locator, _ in chunks)
    assert "消防安全要求" in "".join(content for _, content in chunks)
