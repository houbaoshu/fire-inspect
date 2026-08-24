"""Safe Word template validation, recognition and mapping services."""

from .domain import (
    FillStrategy,
    MappingState,
    RegionLocator,
    RepeatScope,
    TemplateDocumentType,
    TemplateMappingError,
    TemplateMappingRevision,
    TemplateRecognition,
    TemplateRegion,
    add_region,
    confirm_mapping,
    create_mapping_draft,
    revise_mapping,
    update_region,
)
from .recognizer import recognize_template
from .security import (
    DocxLimits,
    DocxSecurityError,
    DocxValidationReport,
    validate_docx,
)

__all__ = [
    "DocxLimits",
    "DocxSecurityError",
    "DocxValidationReport",
    "FillStrategy",
    "MappingState",
    "RegionLocator",
    "RepeatScope",
    "TemplateDocumentType",
    "TemplateMappingError",
    "TemplateMappingRevision",
    "TemplateRecognition",
    "TemplateRegion",
    "add_region",
    "confirm_mapping",
    "create_mapping_draft",
    "recognize_template",
    "revise_mapping",
    "update_region",
    "validate_docx",
]
