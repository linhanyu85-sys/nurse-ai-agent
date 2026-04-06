from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.schemas.document import DocumentDraft, DocumentTemplate
from app.services.standard_forms import normalize_document_type
from app.services.system_templates import system_templates


class DocumentStore:
    def __init__(self) -> None:
        self._items: list[DocumentDraft] = []
        self._templates: list[DocumentTemplate] = []
        self._data_file = Path(__file__).resolve().parents[2] / "data" / "document_store.json"
        self._load()
        if self._ensure_system_templates():
            self._save()

    def _ensure_system_templates(self) -> bool:
        changed = False
        legacy_ids = {"tpl-default-nursing-note"}
        kept_templates: list[DocumentTemplate] = []
        for item in self._templates:
            if item.id in legacy_ids and item.source_type == "system":
                changed = True
                continue
            kept_templates.append(item)
        self._templates = kept_templates
        by_id = {item.id: item for item in self._templates}
        now = datetime.now(timezone.utc)
        for payload in system_templates():
            existing = by_id.get(payload["id"])
            trigger_keywords = list(payload.get("trigger_keywords") or [])
            source_refs = list(payload.get("source_refs") or [])
            if existing is None:
                self._templates.append(
                    DocumentTemplate(
                        id=payload["id"],
                        name=payload["name"],
                        source_type="system",
                        document_type=payload.get("document_type"),
                        trigger_keywords=trigger_keywords,
                        source_refs=source_refs,
                        template_text=payload["template_text"],
                        created_by="system",
                        created_at=now,
                        updated_at=now,
                    )
                )
                changed = True
                continue

            if (
                existing.name != payload["name"]
                or existing.source_type != "system"
                or existing.document_type != payload.get("document_type")
                or existing.trigger_keywords != trigger_keywords
                or existing.source_refs != source_refs
                or existing.template_text != payload["template_text"]
            ):
                existing.name = payload["name"]
                existing.source_type = "system"
                existing.document_type = payload.get("document_type")
                existing.trigger_keywords = trigger_keywords
                existing.source_refs = source_refs
                existing.template_text = payload["template_text"]
                existing.updated_at = now
                changed = True
        return changed

    def create(
        self,
        *,
        patient_id: str,
        encounter_id: str | None,
        document_type: str,
        draft_text: str,
        structured_fields: dict,
        created_by: str | None,
    ) -> DocumentDraft:
        now = datetime.now(timezone.utc)
        item = DocumentDraft(
            id=str(uuid.uuid4()),
            patient_id=patient_id,
            encounter_id=encounter_id,
            document_type=document_type,
            draft_text=draft_text,
            structured_fields=structured_fields,
            status="draft",
            created_by=created_by,
            created_at=now,
            updated_at=now,
        )
        self._items.append(item)
        self._save()
        return item

    def list_by_patient(self, patient_id: str, requested_by: str | None = None) -> list[DocumentDraft]:
        owner = (requested_by or "").strip()
        items = [item for item in reversed(self._items) if item.patient_id == patient_id]
        if owner:
            items = [item for item in items if (item.created_by or "").strip() == owner]
        return items

    def list_history(
        self,
        *,
        patient_id: str | None = None,
        requested_by: str | None = None,
        limit: int = 50,
    ) -> list[DocumentDraft]:
        items = list(reversed(self._items))
        if patient_id:
            items = [item for item in items if item.patient_id == patient_id]
        owner = (requested_by or "").strip()
        if owner:
            items = [item for item in items if (item.created_by or "").strip() == owner]
        return items[:limit]

    def list_inbox(
        self,
        *,
        requested_by: str | None = None,
        patient_id: str | None = None,
        limit: int = 50,
    ) -> list[DocumentDraft]:
        items = list(reversed(self._items))
        items = [item for item in items if item.status != "submitted"]
        if patient_id:
            items = [item for item in items if item.patient_id == patient_id]
        owner = (requested_by or "").strip()
        if owner:
            items = [item for item in items if (item.created_by or "").strip() == owner]
        return items[:limit]

    def get(self, draft_id: str) -> DocumentDraft | None:
        for item in self._items:
            if item.id == draft_id:
                return item
        return None

    def create_template(
        self,
        *,
        name: str,
        template_text: str,
        source_type: str = "import",
        document_type: str | None = None,
        trigger_keywords: list[str] | None = None,
        source_refs: list[str] | None = None,
        created_by: str | None = None,
    ) -> DocumentTemplate:
        now = datetime.now(timezone.utc)
        item = DocumentTemplate(
            id=str(uuid.uuid4()),
            name=name,
            source_type=source_type,
            document_type=document_type,
            trigger_keywords=list(trigger_keywords or []),
            source_refs=list(source_refs or []),
            template_text=template_text,
            created_by=created_by,
            created_at=now,
            updated_at=now,
        )
        self._templates.append(item)
        self._save()
        return item

    def list_templates(self) -> list[DocumentTemplate]:
        return list(reversed(self._templates))

    def get_template(self, template_id: str) -> DocumentTemplate | None:
        for item in self._templates:
            if item.id == template_id:
                return item
        return None

    def update_template(
        self,
        template_id: str,
        *,
        name: str,
        document_type: str | None = None,
        template_text: str,
        trigger_keywords: list[str] | None = None,
        source_refs: list[str] | None = None,
        updated_by: str | None = None,
    ) -> DocumentTemplate | None:
        item = self.get_template(template_id)
        if item is None:
            return None
        item.name = name
        item.document_type = document_type
        item.template_text = template_text
        item.trigger_keywords = list(trigger_keywords or [])
        item.source_refs = list(source_refs or [])
        if updated_by:
            item.created_by = updated_by
        item.updated_at = datetime.now(timezone.utc)
        self._save()
        return item

    def get_preferred_template(self, document_type: str) -> DocumentTemplate | None:
        doc_type = normalize_document_type(document_type)
        exact = [item for item in self._templates if str(item.document_type or "").strip().lower() == doc_type]
        system_exact = [item for item in exact if item.source_type == "system"]
        if system_exact:
            return system_exact[0]
        if exact:
            return exact[0]
        if doc_type != "nursing_note":
            return self.get_preferred_template("nursing_note")
        return None

    def match_template(self, document_type: str, spoken_text: str | None = None) -> DocumentTemplate | None:
        doc_type = normalize_document_type(document_type)
        text = str(spoken_text or "").strip().lower()
        candidates = [item for item in self._templates if str(item.document_type or "").strip().lower() == doc_type]
        if not candidates and doc_type != "nursing_note":
            candidates = [item for item in self._templates if str(item.document_type or "").strip().lower() == "nursing_note"]
        if not candidates:
            return None

        if text:
            scored: list[tuple[int, DocumentTemplate]] = []
            for item in candidates:
                score = 0
                for keyword in item.trigger_keywords:
                    token = str(keyword or "").strip().lower()
                    if token and token in text:
                        score += max(1, len(token))
                if item.source_type == "system":
                    score += 1000
                scored.append((score, item))
            scored.sort(key=lambda pair: pair[0], reverse=True)
            if scored and scored[0][0] > 0:
                return scored[0][1]
        preferred = [item for item in candidates if item.source_type == "system"]
        return preferred[0] if preferred else candidates[0]

    def review(self, draft_id: str, reviewed_by: str) -> DocumentDraft | None:
        item = self.get(draft_id)
        if item is None:
            return None
        item.status = "reviewed"
        item.reviewed_by = reviewed_by
        item.reviewed_at = datetime.now(timezone.utc)
        item.updated_at = item.reviewed_at
        self._save()
        return item

    def submit(self, draft_id: str, submitted_by: str | None = None) -> DocumentDraft | None:
        item = self.get(draft_id)
        if item is None:
            return None
        item.status = "submitted"
        item.updated_at = datetime.now(timezone.utc)
        item.structured_fields = {
            **(item.structured_fields or {}),
            "archive_status": "submitted",
            "archived_at": item.updated_at.isoformat(),
            "archived_by": submitted_by,
        }
        self._save()
        return item

    def edit(
        self,
        draft_id: str,
        draft_text: str,
        edited_by: str | None = None,
        structured_fields: dict | None = None,
        patient_id: str | None = None,
        encounter_id: str | None = None,
    ) -> DocumentDraft | None:
        item = self.get(draft_id)
        if item is None:
            return None
        item.draft_text = draft_text
        item.status = "draft"
        item.updated_at = datetime.now(timezone.utc)
        if patient_id:
            item.patient_id = patient_id
        if encounter_id is not None:
            item.encounter_id = encounter_id or None
        item.structured_fields = {
            **(item.structured_fields or {}),
            **(structured_fields or {}),
            "manual_edited": True,
            "edited_by": edited_by,
            "edited_at": item.updated_at.isoformat(),
        }
        self._save()
        return item

    def _load(self) -> None:
        if not self._data_file.exists():
            return
        try:
            payload = json.loads(self._data_file.read_text(encoding="utf-8"))
            self._items = [DocumentDraft.model_validate(item) for item in payload.get("items", []) if isinstance(item, dict)]
            self._templates = [
                DocumentTemplate.model_validate(item) for item in payload.get("templates", []) if isinstance(item, dict)
            ]
        except Exception:
            self._items = []
            self._templates = []

    def _save(self) -> None:
        self._data_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "items": [item.model_dump(mode="json") for item in self._items[-2000:]],
            "templates": [item.model_dump(mode="json") for item in self._templates[-1000:]],
        }
        self._data_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


document_store = DocumentStore()
