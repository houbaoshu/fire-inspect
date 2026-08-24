from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from .models import KnowledgeClause, KnowledgeDocument


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).lower()
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", value)


def _bigrams(value: str) -> set[str]:
    if len(value) < 2:
        return {value} if value else set()
    return {value[index : index + 2] for index in range(len(value) - 1)}


@dataclass(frozen=True, slots=True)
class SearchResult:
    issue: str
    clause_id: str
    clause_identifier: str
    original_text: str
    applicability_reason: str
    document_title: str
    document_id: str
    page_start: int
    page_end: int
    source_status: str
    score: float
    retrieval_sources: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SearchResponse:
    found: bool
    query: str
    results: tuple[SearchResult, ...]
    message: str


class KnowledgeIndex:
    def __init__(
        self,
        document: KnowledgeDocument,
        clauses: list[KnowledgeClause],
        *,
        minimum_score: float = 0.2,
    ) -> None:
        if any(clause.document_id != document.id for clause in clauses):
            raise ValueError("条款与文档不匹配")
        self.document = document
        self.clauses = tuple(clauses)
        self.minimum_score = minimum_score

    def _score(self, query: str, clause: KnowledgeClause) -> float:
        normalized_query = normalize_text(query)
        normalized_content = normalize_text(clause.content)
        normalized_identifier = normalize_text(clause.clause_identifier)
        if not normalized_query:
            return 0.0
        if normalized_identifier and normalized_identifier in normalized_query:
            return 1.0
        normalized_heading = normalize_text(clause.heading)
        normalized_chapter = normalize_text(clause.chapter)
        normalized_title = normalize_text(self.document.title)
        if normalized_heading and normalized_heading in normalized_query:
            return 0.995
        if normalized_chapter and normalized_chapter in normalized_query:
            return 0.992
        if normalized_title and normalized_title in normalized_query:
            return 0.99
        category_labels = {
            "law": ("法律", "法律法规", "正式法律法规"),
            "standard": ("标准", "标准规范"),
            "local_rule": ("地方规定", "地方性规定"),
            "internal": ("内部制度",),
            "other": ("其他材料",),
        }
        if normalized_query in {
            normalize_text(label) for label in category_labels.get(self.document.category.value, ())
        }:
            return 0.985
        if normalized_query in normalized_content:
            return 0.98
        query_bigrams = _bigrams(normalized_query)
        if not query_bigrams:
            return 0.0
        content_bigrams = _bigrams(normalized_content)
        coverage = len(query_bigrams & content_bigrams) / len(query_bigrams)
        # Long clauses naturally contain incidental matches.  Requiring query
        # coverage keeps unrelated questions below the not-found threshold.
        return round(coverage, 6)

    def search(self, query: str, *, limit: int = 5) -> SearchResponse:
        scored = sorted(
            ((self._score(query, clause), clause) for clause in self.clauses),
            key=lambda item: (-item[0], item[1].sequence),
        )
        accepted = [item for item in scored[:limit] if item[0] >= self.minimum_score]
        if not accepted:
            return SearchResponse(
                found=False,
                query=query,
                results=(),
                message="未找到可靠的相关条款，请调整查询或人工核验。",
            )
        results = tuple(
            SearchResult(
                issue=query,
                clause_id=str(clause.id or ""),
                clause_identifier=clause.clause_identifier,
                original_text=clause.content,
                applicability_reason="查询内容与该条款原文存在可验证的词法对应，请结合事实人工判断适用性。",
                document_title=self.document.title,
                document_id=str(self.document.id),
                page_start=clause.page_start,
                page_end=clause.page_end,
                source_status=self.document.source_label,
                score=score,
            )
            for score, clause in accepted
        )
        return SearchResponse(
            found=True,
            query=query,
            results=results,
            message="已找到可能相关的原文，适用性需人工确认。",
        )
