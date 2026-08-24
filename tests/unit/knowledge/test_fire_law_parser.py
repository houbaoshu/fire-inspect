import json
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import pytest

from fire_inspect.knowledge.models import (
    KnowledgeCategory,
    KnowledgeDocument,
    KnowledgeMetadataError,
)
from fire_inspect.knowledge.parser import parse_fire_law_pdf
from fire_inspect.knowledge.search import KnowledgeIndex

ROOT = Path(__file__).resolve().parents[3]
PDF = ROOT / "testdata/knowledge/reg_ec997c582c9c29a6.pdf"


@pytest.fixture(scope="module")
def parsed_document() -> tuple[KnowledgeDocument, list]:
    document = KnowledgeDocument(
        title="中华人民共和国消防法（测试样本）",
        category=KnowledgeCategory.LAW,
        original_path=PDF,
        sha256="92580dffac57f26f18955271332f819841ba4ad968964626d93146f3094b2576",
    )
    return document, parse_fire_law_pdf(PDF, document.id)


def test_double_column_pdf_yields_articles_one_through_seventy_four(parsed_document) -> None:
    _, clauses = parsed_document
    assert [clause.sequence for clause in clauses] == list(range(1, 75))
    assert clauses[0].clause_identifier == "第一条"
    assert clauses[-1].clause_identifier == "第七十四条"
    assert "预防火灾和减少火灾危害" in clauses[0].content


def test_article_order_does_not_interleave_columns(parsed_document) -> None:
    _, clauses = parsed_document
    assert "国务院应急管理部门" in clauses[3].content
    assert "消防宣传教育" in clauses[5].content
    assert clauses[3].page_start == 1


def test_exact_and_lexical_retrieval_return_verbatim_source(parsed_document) -> None:
    document, clauses = parsed_document
    index = KnowledgeIndex(document, clauses)
    response = index.search("自动消防系统操作人员必须持证上岗")
    assert response.found
    article_21 = next(item for item in response.results if item.clause_identifier == "第二十一条")
    source_clause = clauses[20]
    assert article_21.original_text == source_clause.content
    assert article_21.source_status == "待核验"


def test_index_rejects_clause_from_a_different_document(parsed_document) -> None:
    document, clauses = parsed_document
    mismatched_clause = replace(clauses[0], document_id=uuid4())

    with pytest.raises(ValueError, match="条款与文档不匹配"):
        KnowledgeIndex(document, [mismatched_clause])


def test_unrelated_query_returns_explicit_not_found(parsed_document) -> None:
    document, clauses = parsed_document
    response = KnowledgeIndex(document, clauses, minimum_score=0.3).search(
        "量子传送门的星际消防许可规则"
    )
    assert not response.found
    assert response.results == ()
    assert "未找到" in response.message


def test_machine_readable_knowledge_cases(parsed_document) -> None:
    document, clauses = parsed_document
    with (ROOT / "testdata/expected/knowledge-cases.json").open(encoding="utf-8") as stream:
        cases = json.load(stream)["cases"]
    index = KnowledgeIndex(document, clauses, minimum_score=0.3)
    for case in cases:
        response = index.search(case["query"])
        if case["expected"] == "not_found":
            assert not response.found, case["id"]
            continue
        result = next(
            item for item in response.results if item.clause_identifier == case["expected_clause"]
        )
        assert sha256(result.original_text.encode()).hexdigest() == case["expected_content_sha256"]


def test_internal_policy_cannot_be_misclassified_as_law(tmp_path: Path) -> None:
    with pytest.raises(KnowledgeMetadataError, match="内部制度"):
        KnowledgeDocument(
            title="单位内部值班制度",
            category=KnowledgeCategory.LAW,
            declared_internal=True,
            original_path=tmp_path / "policy.docx",
            sha256="0" * 64,
        )
