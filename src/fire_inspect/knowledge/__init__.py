"""Knowledge ingestion and deterministic retrieval services."""

from .models import (
    KnowledgeCategory,
    KnowledgeClause,
    KnowledgeDocument,
    SourceVerification,
    Validity,
)
from .parser import parse_fire_law_pdf, parse_text_docx, parse_text_pdf
from .search import KnowledgeIndex, SearchResponse, SearchResult

__all__ = [
    "KnowledgeCategory",
    "KnowledgeClause",
    "KnowledgeDocument",
    "KnowledgeIndex",
    "SearchResponse",
    "SearchResult",
    "SourceVerification",
    "Validity",
    "parse_fire_law_pdf",
    "parse_text_docx",
    "parse_text_pdf",
]
