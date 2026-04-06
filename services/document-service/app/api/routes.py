import asyncio
import logging
import re
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status

from app.core.config import settings
from app.schemas.document import (
    DocumentDraft,
    DocumentTemplate,
    DraftEditRequest,
    DraftRequest,
    DraftReviewRequest,
    DraftSubmitRequest,
    StandardFormBundle,
    TemplateImportRequest,
    TemplateUpdateRequest,
)
from app.services.client import fetch_bed_context, fetch_patient_context, write_audit_log
from app.services.db_store import document_db_store
from app.services.generator import build_document_draft
from app.services.llm_client import hydrate_legacy_structured_fields
from app.services.standard_form_bundle import build_standard_form_bundle, list_standard_form_bundles
from app.services.standard_forms import normalize_document_type
from app.services.store import document_store
from app.services.template_parser import parse_template_import

router = APIRouter()
logger = logging.getLogger(__name__)


_BED_TEXT_PATTERNS = (
    re.compile(r"(?:床号|床位)[:：]\s*([A-Za-z]?\d{1,3})"),
    re.compile(r"\b([A-Za-z]?\d{1,3})床\b"),
)
_PATIENT_NAME_PATTERNS = (
    re.compile(r"(?:患者姓名|姓名)[:：]\s*([^\s，。,；;:：]+)"),
)
_IDENTIFIER_PATTERNS = {
    "mrn": (
        re.compile(r"(?:病案号|MRN)[:：]\s*([A-Za-z0-9-]+)", re.IGNORECASE),
    ),
    "inpatient_no": (
        re.compile(r"(?:住院号|住院编号|IP)[:：]\s*([A-Za-z0-9-]+)", re.IGNORECASE),
    ),
}


def _normalize_user_id(value: str | None) -> str:
    raw = (value or "").strip()
    if not raw:
        return "u_linmeili"
    if raw.startswith("u_"):
        return raw
    return f"u_{raw}"


def _extract_spoken_text(draft_text: str) -> str:
    raw = str(draft_text or "")
    for label in ("护理记录", "护理措施与效果", "24小时病情及处理", "补充说明", "备注"):
        for marker in (f"{label}:", f"{label}："):
            if marker in raw:
                return raw.split(marker, 1)[1].split("\n", 1)[0].strip()
    return ""


def _extract_bed_no_from_draft_text(draft_text: str) -> str | None:
    raw = str(draft_text or "")
    for pattern in _BED_TEXT_PATTERNS:
        match = pattern.search(raw)
        if not match:
            continue
        value = str(match.group(1) or "").strip()
        if value:
            return value
    return None


def _extract_patient_name_from_draft_text(draft_text: str) -> str | None:
    raw = str(draft_text or "")
    for pattern in _PATIENT_NAME_PATTERNS:
        match = pattern.search(raw)
        if not match:
            continue
        value = _safe_structured_text(match.group(1))
        if value and "?" not in value:
            return value
    return None


def _extract_identifier_from_draft_text(draft_text: str, kind: str) -> str | None:
    raw = str(draft_text or "")
    for pattern in _IDENTIFIER_PATTERNS.get(kind, ()):
        match = pattern.search(raw)
        if not match:
            continue
        value = _safe_structured_text(match.group(1))
        if value:
            return value
    return None


def _safe_structured_text(value: object) -> str | None:
    text = str(value or "").strip()
    if _is_placeholder_text(text):
        return None
    return text or None


def _is_placeholder_text(value: object) -> bool:
    text = str(value or "").strip()
    if not text:
        return True
    lowered = text.lower()
    if re.fullmatch(r"[?？]+", text):
        return True
    if re.search(r"\{\{\s*[^{}]+\s*\}\}", text) or re.fullmatch(r"\{[^{}]+\}", text):
        return True
    visible = re.sub(r"\s+", "", text)
    garbled_count = len(re.findall(r"[?？\uFFFD]", visible))
    if len(visible) >= 6 and garbled_count * 10 >= len(visible) * 4:
        return True
    return lowered in {
        "-",
        "待补充",
        "待完善",
        "待评估",
        "待签名",
        "待处理",
        "暂无",
        "无",
        "未知",
        "未填写",
        "未提供",
        "none",
        "null",
        "n/a",
        "na",
        "undefined",
    }


def _clean_structured_placeholders(value: object) -> object:
    if isinstance(value, dict):
        return {key: _clean_structured_placeholders(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_clean_structured_placeholders(item) for item in value]
    if isinstance(value, str):
        return "" if _is_placeholder_text(value) else value
    return value


def _build_legacy_context_snapshot(item: DocumentDraft) -> dict:
    structured = dict(item.structured_fields or {})
    parsed_patient_name = _extract_patient_name_from_draft_text(item.draft_text)
    parsed_bed_no = _extract_bed_no_from_draft_text(item.draft_text)
    parsed_mrn = _extract_identifier_from_draft_text(item.draft_text, "mrn")
    parsed_inpatient_no = _extract_identifier_from_draft_text(item.draft_text, "inpatient_no")
    return {
        "patient_id": item.patient_id,
        "encounter_id": item.encounter_id,
        "patient_name": _safe_structured_text(structured.get("patient_name") or structured.get("full_name")) or parsed_patient_name,
        "full_name": _safe_structured_text(structured.get("full_name") or structured.get("patient_name")) or parsed_patient_name,
        "bed_no": _safe_structured_text(structured.get("bed_no")) or parsed_bed_no,
        "mrn": _safe_structured_text(structured.get("mrn")) or parsed_mrn,
        "inpatient_no": _safe_structured_text(structured.get("inpatient_no")) or parsed_inpatient_no,
        "gender": _safe_structured_text(structured.get("gender")),
        "age": structured.get("age"),
        "blood_type": _safe_structured_text(structured.get("blood_type")),
        "allergy_info": _safe_structured_text(structured.get("allergy_info")),
        "diagnoses": _normalize_list_field(structured.get("diagnoses")),
        "risk_tags": _normalize_list_field(structured.get("risk_tags")),
        "pending_tasks": _normalize_list_field(structured.get("pending_tasks")),
        "latest_observations": _normalize_list_field(structured.get("latest_observations")),
        "requested_by": _safe_structured_text(structured.get("requested_by")),
    }


async def _hydrate_legacy_draft(item: DocumentDraft, context_cache: dict[str, dict]) -> DocumentDraft:
    try:
        raw_structured = dict(item.structured_fields or {})
        structured = _clean_structured_placeholders(raw_structured)
        editable_blocks = structured.get("editable_blocks")
        standard_form = structured.get("standard_form")
        has_standard_editor = isinstance(editable_blocks, list) and editable_blocks and isinstance(standard_form, dict)
        requires_refresh = (
            not has_standard_editor
            or structured != raw_structured
            or _safe_structured_text(structured.get("patient_name") or structured.get("full_name")) is None
            or _safe_structured_text(structured.get("bed_no")) is None
        )
        if not requires_refresh:
            return item

        context = context_cache.get(item.patient_id)
        if context is None:
            context = await fetch_patient_context(item.patient_id) or _build_legacy_context_snapshot(item)
            context_cache[item.patient_id] = context

        item.structured_fields = hydrate_legacy_structured_fields(
            document_type=item.document_type,
            context=context,
            draft_text=item.draft_text,
            spoken_text=str(structured.get("spoken_text") or _extract_spoken_text(item.draft_text)).strip() or None,
            template_name=str(structured.get("template_name") or "").strip() or None,
            existing_fields=structured,
        )
        return item
    except Exception as exc:
        logger.warning("document_service_legacy_draft_hydration_failed draft_id=%s error=%s", item.id, exc)
        return item


async def _hydrate_legacy_draft_fast(item: DocumentDraft) -> DocumentDraft:
    try:
        raw_structured = dict(item.structured_fields or {})
        structured = _clean_structured_placeholders(raw_structured)
        editable_blocks = structured.get("editable_blocks")
        standard_form = structured.get("standard_form")
        has_standard_editor = isinstance(editable_blocks, list) and editable_blocks and isinstance(standard_form, dict)
        requires_refresh = (
            not has_standard_editor
            or structured != raw_structured
            or _safe_structured_text(structured.get("patient_name") or structured.get("full_name")) is None
            or _safe_structured_text(structured.get("bed_no")) is None
        )
        if not requires_refresh:
            return item

        snapshot_context = _build_legacy_context_snapshot(item)
        item.structured_fields = hydrate_legacy_structured_fields(
            document_type=item.document_type,
            context=snapshot_context,
            draft_text=item.draft_text,
            spoken_text=str(structured.get("spoken_text") or _extract_spoken_text(item.draft_text)).strip() or None,
            template_name=str(structured.get("template_name") or "").strip() or None,
            existing_fields=structured,
        )
        return item
    except Exception as exc:
        logger.warning("document_service_legacy_draft_fast_hydration_failed draft_id=%s error=%s", item.id, exc)
        return item


def _normalize_list_field(value: object) -> list:
    return list(value) if isinstance(value, list) else []


def _build_draft_context(
    payload: DraftRequest,
    *,
    owner: str,
    base_context: dict | None,
    now: datetime,
) -> tuple[dict, str]:
    context = dict(base_context or {})
    resolved_bed_no = str(payload.bed_no or context.get("bed_no") or "").strip()
    resolved_patient_name = str(payload.patient_name or context.get("patient_name") or context.get("full_name") or "").strip()
    context_source = "live_patient_context" if base_context else "payload_fallback"
    resolved_context = {
        **context,
        "patient_id": payload.patient_id,
        "encounter_id": context.get("encounter_id"),
        "bed_no": resolved_bed_no or None,
        "patient_name": resolved_patient_name or None,
        "full_name": str(context.get("full_name") or resolved_patient_name or "").strip() or None,
        "diagnoses": _normalize_list_field(context.get("diagnoses")),
        "risk_tags": _normalize_list_field(context.get("risk_tags")),
        "pending_tasks": _normalize_list_field(context.get("pending_tasks")),
        "latest_observations": _normalize_list_field(context.get("latest_observations")),
        "requested_by": owner,
        "current_time": now.strftime("%Y-%m-%d %H:%M"),
        "chart_date": now.strftime("%Y-%m-%d"),
        "shift_date": date.today().isoformat(),
        "context_source": context_source,
    }
    return resolved_context, context_source


def _extract_editable_block_value(structured_fields: dict[str, Any], key: str) -> str | None:
    blocks = structured_fields.get("editable_blocks")
    if not isinstance(blocks, list):
        return None
    for block in blocks:
        if not isinstance(block, dict):
            continue
        if str(block.get("key") or "").strip() != key:
            continue
        return _safe_structured_text(block.get("value"))
    return None


async def _resolve_draft_binding(
    *,
    item: DocumentDraft | None,
    document_type: str,
    draft_text: str,
    structured_patch: dict[str, Any] | None,
    owner: str | None,
    bed_context_cache: dict[str, dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], str | None, str | None]:
    merged_fields = _clean_structured_placeholders(
        {
            **(dict(item.structured_fields or {}) if item else {}),
            **dict(structured_patch or {}),
        }
    )
    if owner and not str(merged_fields.get("requested_by") or "").strip():
        merged_fields["requested_by"] = owner

    bed_no = _safe_structured_text(merged_fields.get("bed_no")) or _extract_editable_block_value(merged_fields, "bed_no")
    live_bed_context: dict[str, Any] | None = None
    if bed_no:
        cache_key = str(bed_no).strip()
        if bed_context_cache is not None:
            live_bed_context = bed_context_cache.get(cache_key)
        if live_bed_context is None:
            live_bed_context = await fetch_bed_context(cache_key, requested_by=owner)
            if bed_context_cache is not None and live_bed_context:
                bed_context_cache[cache_key] = live_bed_context

    resolved_patient_id = str(
        (live_bed_context or {}).get("patient_id") or (item.patient_id if item else "") or merged_fields.get("patient_id") or ""
    ).strip() or None
    resolved_encounter_id = str(
        (live_bed_context or {}).get("encounter_id") or (item.encounter_id if item else "") or merged_fields.get("encounter_id") or ""
    ).strip() or None

    resolved_context: dict[str, Any] | None = None
    if live_bed_context:
        patient_name = _safe_structured_text(live_bed_context.get("patient_name") or live_bed_context.get("full_name"))
        resolved_context = {
            **live_bed_context,
            "patient_id": resolved_patient_id,
            "encounter_id": resolved_encounter_id,
            "patient_name": patient_name,
            "full_name": patient_name,
        }
        merged_fields = {
            **merged_fields,
            "binding_source": "live_bed_context",
            "patient_id": resolved_patient_id,
            "encounter_id": resolved_encounter_id,
            "bed_no": _safe_structured_text(live_bed_context.get("bed_no")) or bed_no,
            "patient_name": patient_name,
            "full_name": patient_name,
            "mrn": _safe_structured_text(live_bed_context.get("mrn")) or merged_fields.get("mrn"),
            "inpatient_no": _safe_structured_text(live_bed_context.get("inpatient_no")) or merged_fields.get("inpatient_no"),
            "gender": _safe_structured_text(live_bed_context.get("gender")) or merged_fields.get("gender"),
            "age": live_bed_context.get("age") if live_bed_context.get("age") is not None else merged_fields.get("age"),
            "blood_type": _safe_structured_text(live_bed_context.get("blood_type")) or merged_fields.get("blood_type"),
            "allergy_info": _safe_structured_text(live_bed_context.get("allergy_info")) or merged_fields.get("allergy_info"),
            "diagnoses": _normalize_list_field(live_bed_context.get("diagnoses")) or _normalize_list_field(merged_fields.get("diagnoses")),
            "risk_tags": _normalize_list_field(live_bed_context.get("risk_tags")) or _normalize_list_field(merged_fields.get("risk_tags")),
            "pending_tasks": _normalize_list_field(live_bed_context.get("pending_tasks")) or _normalize_list_field(merged_fields.get("pending_tasks")),
            "latest_observations": _normalize_list_field(live_bed_context.get("latest_observations"))
            or _normalize_list_field(merged_fields.get("latest_observations")),
        }
    elif resolved_patient_id:
        resolved_context = await fetch_patient_context(resolved_patient_id)
        if resolved_context:
            merged_fields.setdefault("patient_name", resolved_context.get("patient_name"))
            merged_fields.setdefault("full_name", resolved_context.get("full_name") or resolved_context.get("patient_name"))
            merged_fields.setdefault("bed_no", resolved_context.get("bed_no"))
            merged_fields.setdefault("mrn", resolved_context.get("mrn"))
            merged_fields.setdefault("inpatient_no", resolved_context.get("inpatient_no"))

    hydrated_fields = hydrate_legacy_structured_fields(
        document_type=document_type,
        context=resolved_context or _build_legacy_context_snapshot(item) if item else merged_fields,
        draft_text=draft_text,
        spoken_text=str(merged_fields.get("spoken_text") or _extract_spoken_text(draft_text)).strip() or None,
        template_name=str(merged_fields.get("template_name") or "").strip() or None,
        existing_fields=merged_fields,
    )
    return hydrated_fields, resolved_patient_id, resolved_encounter_id


async def _bind_draft_for_response(
    item: DocumentDraft,
    *,
    owner: str | None = None,
    bed_context_cache: dict[str, dict[str, Any]] | None = None,
) -> DocumentDraft:
    structured_fields, patient_id, encounter_id = await _resolve_draft_binding(
        item=item,
        document_type=item.document_type,
        draft_text=item.draft_text,
        structured_patch=item.structured_fields or {},
        owner=owner,
        bed_context_cache=bed_context_cache,
    )
    item.structured_fields = structured_fields
    if patient_id:
        item.patient_id = patient_id
    if encounter_id is not None:
        item.encounter_id = encounter_id
    return item


async def _bind_draft_for_list_response(
    item: DocumentDraft,
    *,
    owner: str | None = None,
    bed_context_cache: dict[str, dict[str, Any]] | None = None,
) -> DocumentDraft:
    try:
        structured_fields = _clean_structured_placeholders(dict(item.structured_fields or {}))
        hydrated_fields = hydrate_legacy_structured_fields(
            document_type=item.document_type,
            context=_build_legacy_context_snapshot(item),
            draft_text=item.draft_text,
            spoken_text=str(structured_fields.get("spoken_text") or _extract_spoken_text(item.draft_text)).strip() or None,
            template_name=str(structured_fields.get("template_name") or "").strip() or None,
            existing_fields=structured_fields,
        )
        bed_no = _safe_structured_text(hydrated_fields.get("bed_no")) or _extract_bed_no_from_draft_text(item.draft_text)
        live_bed_context: dict[str, Any] | None = None
        needs_live_bed_context = bool(
            bed_no
            and (
                not _safe_structured_text(hydrated_fields.get("patient_id") or item.patient_id)
                or not _safe_structured_text(hydrated_fields.get("encounter_id") or item.encounter_id)
                or not _safe_structured_text(hydrated_fields.get("patient_name") or hydrated_fields.get("full_name"))
            )
        )
        if needs_live_bed_context:
            cache_key = str(bed_no).strip()
            if bed_context_cache is not None:
                live_bed_context = bed_context_cache.get(cache_key)
            if live_bed_context is None:
                live_bed_context = await fetch_bed_context(cache_key, requested_by=owner)
                if bed_context_cache is not None and live_bed_context:
                    bed_context_cache[cache_key] = live_bed_context

        if live_bed_context:
            resolved_patient_id = _safe_structured_text(live_bed_context.get("patient_id")) or item.patient_id
            resolved_encounter_id = _safe_structured_text(live_bed_context.get("encounter_id")) or item.encounter_id
            patient_name = _safe_structured_text(live_bed_context.get("patient_name") or live_bed_context.get("full_name"))
            hydrated_fields = hydrate_legacy_structured_fields(
                document_type=item.document_type,
                context={
                    **live_bed_context,
                    "patient_id": resolved_patient_id,
                    "encounter_id": resolved_encounter_id,
                    "patient_name": patient_name,
                    "full_name": patient_name,
                },
                draft_text=item.draft_text,
                spoken_text=str(hydrated_fields.get("spoken_text") or _extract_spoken_text(item.draft_text)).strip() or None,
                template_name=str(hydrated_fields.get("template_name") or "").strip() or None,
                existing_fields={
                    **hydrated_fields,
                    "binding_source": "live_bed_context",
                    "patient_id": resolved_patient_id,
                    "encounter_id": resolved_encounter_id,
                    "bed_no": _safe_structured_text(live_bed_context.get("bed_no")) or bed_no,
                    "patient_name": patient_name,
                    "full_name": patient_name,
                    "mrn": _safe_structured_text(live_bed_context.get("mrn")) or hydrated_fields.get("mrn"),
                    "inpatient_no": _safe_structured_text(live_bed_context.get("inpatient_no")) or hydrated_fields.get("inpatient_no"),
                    "gender": _safe_structured_text(live_bed_context.get("gender")) or hydrated_fields.get("gender"),
                    "age": live_bed_context.get("age") if live_bed_context.get("age") is not None else hydrated_fields.get("age"),
                    "blood_type": _safe_structured_text(live_bed_context.get("blood_type")) or hydrated_fields.get("blood_type"),
                    "allergy_info": _safe_structured_text(live_bed_context.get("allergy_info")) or hydrated_fields.get("allergy_info"),
                    "diagnoses": _normalize_list_field(live_bed_context.get("diagnoses")) or _normalize_list_field(hydrated_fields.get("diagnoses")),
                    "risk_tags": _normalize_list_field(live_bed_context.get("risk_tags")) or _normalize_list_field(hydrated_fields.get("risk_tags")),
                    "pending_tasks": _normalize_list_field(live_bed_context.get("pending_tasks"))
                    or _normalize_list_field(hydrated_fields.get("pending_tasks")),
                    "latest_observations": _normalize_list_field(live_bed_context.get("latest_observations"))
                    or _normalize_list_field(hydrated_fields.get("latest_observations")),
                },
            )
        item.structured_fields = hydrated_fields
        patient_id = _safe_structured_text(
            hydrated_fields.get("patient_id")
            or hydrated_fields.get("patient_uuid")
            or hydrated_fields.get("current_patient_id")
        )
        encounter_id = _safe_structured_text(hydrated_fields.get("encounter_id"))
        if patient_id:
            item.patient_id = patient_id
        if encounter_id:
            item.encounter_id = encounter_id
        return item
    except Exception as exc:
        logger.warning("document_service_list_binding_failed draft_id=%s error=%s", item.id, exc)
        return item


async def _get_existing_draft(draft_id: str) -> DocumentDraft | None:
    item = await document_db_store.get(draft_id)
    if item is not None:
        return item
    return document_store.get(draft_id)


@router.get("/health")
def health() -> dict:
    return {"status": "ok", "service": settings.service_name}


@router.get("/ready")
def ready() -> dict:
    return {"status": "ready", "service": settings.service_name}


@router.get("/version")
def version() -> dict:
    return {
        "service": settings.service_name,
        "version": settings.app_version,
        "env": settings.app_env,
        "mock_mode": settings.mock_mode,
        "local_only_mode": settings.local_only_mode,
    }


@router.post("/document/draft", response_model=DocumentDraft)
async def create_draft(payload: DraftRequest) -> DocumentDraft:
    owner = _normalize_user_id(payload.requested_by)
    now = datetime.now()
    base_context = await fetch_patient_context(payload.patient_id)
    context, context_source = _build_draft_context(payload, owner=owner, base_context=base_context, now=now)
    template_text: str | None = payload.template_text
    template_name: str | None = payload.template_name
    resolved_template_id: str | None = payload.template_id
    document_type = normalize_document_type(payload.document_type)
    selected_template = None

    if payload.template_id:
        template = document_store.get_template(payload.template_id)
        if template is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="template_not_found")
        selected_template = template
    elif not template_text:
        preferred_template = document_store.get_preferred_template(document_type)
        if preferred_template is not None:
            selected_template = preferred_template
        else:
            matched_template = document_store.match_template(document_type, payload.spoken_text)
            if matched_template is not None:
                selected_template = matched_template

    if selected_template is not None:
        template_text = selected_template.template_text
        template_name = selected_template.name
        resolved_template_id = selected_template.id
        document_type = normalize_document_type(selected_template.document_type or document_type)

    draft_text, structured_fields = await build_document_draft(
        document_type=document_type,
        spoken_text=payload.spoken_text,
        context=context,
        template_text=template_text,
        template_name=template_name,
    )
    template_locked = bool(selected_template and selected_template.source_type == "system")
    structured_fields = {
        **structured_fields,
        "context_source": context_source,
        "patient_id": payload.patient_id,
        "patient_name": context.get("patient_name"),
        "bed_no": context.get("bed_no"),
        "requested_by": owner,
        "template_id": resolved_template_id,
        "template_name": template_name or structured_fields.get("template_name"),
        "template_source_type": selected_template.source_type if selected_template is not None else None,
        "template_source_refs": list(selected_template.source_refs or []) if selected_template is not None else [],
        "template_snapshot": template_text,
    }
    if template_locked and selected_template is not None:
        structured_fields = {
            **structured_fields,
            "template_locked": True,
            "template_source_policy": "system_standard_locked",
        }
    structured_fields = hydrate_legacy_structured_fields(
        document_type=document_type,
        context=context,
        draft_text=draft_text,
        spoken_text=payload.spoken_text,
        template_name=template_name or str(structured_fields.get("template_name") or "").strip() or None,
        existing_fields=_clean_structured_placeholders(structured_fields),
    )
    item = await document_db_store.create(
        patient_id=payload.patient_id,
        encounter_id=context.get("encounter_id"),
        document_type=document_type,
        draft_text=draft_text,
        structured_fields=structured_fields,
        created_by=owner,
    )
    if item is None:
        item = document_store.create(
            patient_id=payload.patient_id,
            encounter_id=context.get("encounter_id"),
            document_type=document_type,
            draft_text=draft_text,
            structured_fields=structured_fields,
            created_by=owner,
        )
    await write_audit_log(
        action="document.draft.create",
        resource_type="document_draft",
        resource_id=item.id,
        detail={
            "patient_id": payload.patient_id,
            "document_type": document_type,
            "template_id": resolved_template_id,
            "template_name": template_name,
            "template_locked": template_locked,
            "context_source": context_source,
            "bed_no": context.get("bed_no"),
        },
        user_id=owner,
    )
    return item


@router.post("/document/template/import", response_model=DocumentTemplate)
async def import_template(payload: TemplateImportRequest) -> DocumentTemplate:
    try:
        name, template_text = parse_template_import(payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    item = document_store.create_template(
        name=name,
        template_text=template_text,
        source_type="import",
        document_type=normalize_document_type(payload.document_type) if payload.document_type else None,
        trigger_keywords=list(payload.trigger_keywords or []),
        source_refs=list(payload.source_refs or []),
        created_by=_normalize_user_id(payload.requested_by),
    )
    await write_audit_log(
        action="document.template.import",
        resource_type="document_template",
        resource_id=item.id,
        detail={"name": item.name, "length": len(item.template_text)},
        user_id=_normalize_user_id(payload.requested_by),
    )
    return item


@router.get("/document/templates", response_model=list[DocumentTemplate])
async def list_templates() -> list[DocumentTemplate]:
    return document_store.list_templates()


@router.get("/document/templates/{template_id}", response_model=DocumentTemplate)
async def get_template(template_id: str) -> DocumentTemplate:
    item = document_store.get_template(template_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="template_not_found")
    return item


@router.post("/document/templates/{template_id}/update", response_model=DocumentTemplate)
async def update_template(template_id: str, payload: TemplateUpdateRequest) -> DocumentTemplate:
    item = document_store.update_template(
        template_id,
        name=payload.name,
        document_type=normalize_document_type(payload.document_type) if payload.document_type else None,
        template_text=payload.template_text,
        trigger_keywords=list(payload.trigger_keywords or []),
        source_refs=list(payload.source_refs or []),
        updated_by=_normalize_user_id(payload.requested_by),
    )
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="template_not_found")
    await write_audit_log(
        action="document.template.update",
        resource_type="document_template",
        resource_id=item.id,
        detail={"name": item.name, "length": len(item.template_text)},
        user_id=_normalize_user_id(payload.requested_by),
    )
    return item


@router.get("/document/standard-forms", response_model=list[StandardFormBundle])
async def list_standard_forms() -> list[StandardFormBundle]:
    return [StandardFormBundle.model_validate(item) for item in list_standard_form_bundles()]


@router.get("/document/standard-forms/{document_type}", response_model=StandardFormBundle)
async def get_standard_form(document_type: str) -> StandardFormBundle:
    try:
        bundle = build_standard_form_bundle(document_type)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="standard_form_not_found") from exc
    return StandardFormBundle.model_validate(bundle)


@router.get("/document/drafts/{patient_id}", response_model=list[DocumentDraft])
async def list_drafts(patient_id: str, requested_by: str | None = Query(default=None)) -> list[DocumentDraft]:
    owner = _normalize_user_id(requested_by) if requested_by else None
    db_items = await document_db_store.list_by_patient(patient_id, requested_by=owner)
    items = db_items if db_items is not None else document_store.list_by_patient(patient_id, requested_by=owner)
    hydrated_items = await asyncio.gather(*[_hydrate_legacy_draft_fast(item) for item in items])
    bed_context_cache: dict[str, dict[str, Any]] = {}
    return await asyncio.gather(
        *[_bind_draft_for_list_response(item, owner=owner, bed_context_cache=bed_context_cache) for item in hydrated_items]
    )


@router.get("/document/history", response_model=list[DocumentDraft])
async def document_history(
    patient_id: str | None = Query(default=None),
    requested_by: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[DocumentDraft]:
    owner = _normalize_user_id(requested_by) if requested_by else None
    db_items = await document_db_store.list_history(patient_id=patient_id, requested_by=owner, limit=limit)
    items = db_items if db_items is not None else document_store.list_history(patient_id=patient_id, requested_by=owner, limit=limit)
    hydrated_items = await asyncio.gather(*[_hydrate_legacy_draft_fast(item) for item in items])
    bed_context_cache: dict[str, dict[str, Any]] = {}
    return await asyncio.gather(
        *[_bind_draft_for_list_response(item, owner=owner, bed_context_cache=bed_context_cache) for item in hydrated_items]
    )


@router.get("/document/inbox/{requested_by}", response_model=list[DocumentDraft])
async def document_inbox(
    requested_by: str,
    patient_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[DocumentDraft]:
    owner = _normalize_user_id(requested_by)
    db_items = await document_db_store.list_inbox(requested_by=owner, patient_id=patient_id, limit=limit)
    items = db_items if db_items is not None else document_store.list_inbox(requested_by=owner, patient_id=patient_id, limit=limit)
    hydrated_items = await asyncio.gather(*[_hydrate_legacy_draft_fast(item) for item in items])
    bed_context_cache: dict[str, dict[str, Any]] = {}
    return await asyncio.gather(
        *[_bind_draft_for_list_response(item, owner=owner, bed_context_cache=bed_context_cache) for item in hydrated_items]
    )


@router.post("/document/{draft_id}/review", response_model=DocumentDraft)
async def review_draft(draft_id: str, payload: DraftReviewRequest) -> DocumentDraft:
    owner = _normalize_user_id(payload.reviewed_by)
    existing_item = await _get_existing_draft(draft_id)
    if existing_item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="draft_not_found")
    rebinding_fields, resolved_patient_id, resolved_encounter_id = await _resolve_draft_binding(
        item=existing_item,
        document_type=existing_item.document_type,
        draft_text=existing_item.draft_text,
        structured_patch=existing_item.structured_fields or {},
        owner=owner,
    )
    item = await document_db_store.edit(
        draft_id,
        existing_item.draft_text,
        edited_by=owner,
        structured_fields=rebinding_fields,
        patient_id=resolved_patient_id,
        encounter_id=resolved_encounter_id,
    )
    if item is None:
        item = document_store.edit(
            draft_id,
            existing_item.draft_text,
            edited_by=owner,
            structured_fields=rebinding_fields,
            patient_id=resolved_patient_id,
            encounter_id=resolved_encounter_id,
        )
    item = await document_db_store.review(draft_id, owner)
    if item is None:
        item = document_store.review(draft_id, owner)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="draft_not_found")
    await write_audit_log(
        action="document.draft.review",
        resource_type="document_draft",
        resource_id=draft_id,
        detail={"review_note": payload.review_note},
        user_id=owner,
    )
    return item


@router.post("/document/{draft_id}/submit", response_model=DocumentDraft)
async def submit_draft(draft_id: str, payload: DraftSubmitRequest) -> DocumentDraft:
    owner = _normalize_user_id(payload.submitted_by)
    existing_item = await _get_existing_draft(draft_id)
    if existing_item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="draft_not_found")
    rebinding_fields, resolved_patient_id, resolved_encounter_id = await _resolve_draft_binding(
        item=existing_item,
        document_type=existing_item.document_type,
        draft_text=existing_item.draft_text,
        structured_patch=existing_item.structured_fields or {},
        owner=owner,
    )
    item = await document_db_store.edit(
        draft_id,
        existing_item.draft_text,
        edited_by=owner,
        structured_fields=rebinding_fields,
        patient_id=resolved_patient_id,
        encounter_id=resolved_encounter_id,
    )
    if item is None:
        item = document_store.edit(
            draft_id,
            existing_item.draft_text,
            edited_by=owner,
            structured_fields=rebinding_fields,
            patient_id=resolved_patient_id,
            encounter_id=resolved_encounter_id,
        )
    item = await document_db_store.submit(draft_id, submitted_by=owner)
    if item is None:
        item = document_store.submit(draft_id, submitted_by=owner)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="draft_not_found")
    await write_audit_log(
        action="document.draft.submit",
        resource_type="document_draft",
        resource_id=draft_id,
        detail={"status": item.status},
        user_id=owner,
    )
    return item


@router.post("/document/{draft_id}/edit", response_model=DocumentDraft)
async def edit_draft(draft_id: str, payload: DraftEditRequest) -> DocumentDraft:
    edited_by = _normalize_user_id(payload.edited_by) if payload.edited_by else None
    existing_item = await _get_existing_draft(draft_id)
    if existing_item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="draft_not_found")
    structured_patch, resolved_patient_id, resolved_encounter_id = await _resolve_draft_binding(
        item=existing_item,
        document_type=existing_item.document_type,
        draft_text=payload.draft_text,
        structured_patch=dict(payload.structured_fields or {}),
        owner=edited_by,
    )
    item = await document_db_store.edit(
        draft_id,
        payload.draft_text,
        edited_by=edited_by,
        structured_fields=structured_patch,
        patient_id=resolved_patient_id,
        encounter_id=resolved_encounter_id,
    )
    if item is None:
        item = document_store.edit(
            draft_id,
            payload.draft_text,
            edited_by,
            structured_fields=structured_patch,
            patient_id=resolved_patient_id,
            encounter_id=resolved_encounter_id,
        )
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="draft_not_found")
    await write_audit_log(
        action="document.draft.edit",
        resource_type="document_draft",
        resource_id=draft_id,
        detail={"edited_by": edited_by, "length": len(payload.draft_text)},
        user_id=edited_by,
    )
    return item
