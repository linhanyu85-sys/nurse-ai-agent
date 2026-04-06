from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class DraftRequest(BaseModel):
    patient_id: str
    document_type: str = "nursing_note"
    spoken_text: str | None = None
    template_id: str | None = None
    template_text: str | None = None
    template_name: str | None = None
    requested_by: str | None = None
    bed_no: str | None = None
    patient_name: str | None = None


class DraftReviewRequest(BaseModel):
    reviewed_by: str
    review_note: str | None = None


class DraftSubmitRequest(BaseModel):
    submitted_by: str


class DraftEditRequest(BaseModel):
    draft_text: str
    edited_by: str | None = None
    structured_fields: dict[str, Any] | None = None


class TemplateImportRequest(BaseModel):
    name: str | None = None
    document_type: str | None = None
    template_text: str | None = None
    template_base64: str | None = None
    file_name: str | None = None
    mime_type: str | None = None
    trigger_keywords: list[str] | None = None
    source_refs: list[str] | None = None
    requested_by: str | None = None


class TemplateUpdateRequest(BaseModel):
    name: str
    document_type: str | None = None
    template_text: str
    trigger_keywords: list[str] | None = None
    source_refs: list[str] | None = None
    requested_by: str | None = None


class DocumentTemplate(BaseModel):
    id: str
    name: str
    source_type: str = "import"
    document_type: str | None = None
    trigger_keywords: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)
    template_text: str
    created_by: str | None = None
    created_at: datetime
    updated_at: datetime


class StandardFormBundle(BaseModel):
    document_type: str
    form_id: str
    name: str
    standard_family: str | None = None
    description: str | None = None
    schema_version: str | None = None
    source_refs: list[str] = Field(default_factory=list)
    sections: list[dict[str, Any]] = Field(default_factory=list)
    field_count: int = 0
    sheet_columns: list[dict[str, Any]] = Field(default_factory=list)
    questionnaire: dict[str, Any] = Field(default_factory=dict)


class DocumentDraft(BaseModel):
    id: str
    patient_id: str
    encounter_id: str | None = None
    document_type: str
    draft_text: str
    structured_fields: dict[str, Any] = Field(default_factory=dict)
    source_type: str = "ai"
    status: str = "draft"
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    created_by: str | None = None
    created_at: datetime
    updated_at: datetime
