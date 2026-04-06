from __future__ import annotations

import json
import re
import time
from typing import Any

import httpx

from app.core.config import settings
from app.services.standard_form_bundle import build_standard_form_questionnaire
from app.services.standard_forms import get_field_schema, get_standard_form_definition, normalize_document_type

DOC_LOCAL_RENDER_TIMEOUT_SEC = 8
LOCAL_MODEL_CACHE_TTL_SEC = 20.0
MANUAL_REVIEW_NOTICE = "[AI提示] 该草稿需护士人工复核后提交。"

_local_model_probe_cache: dict[str, Any] = {
    "ts": 0.0,
    "models": set(),
}

MISSING_VALUE_TOKENS = {"", "-", "待补充", "待评估", "待签名", "待处理"}


def _safe_string(value: Any, fallback: str = "待补充") -> str:
    text = str(value or "").strip()
    return text or fallback


def _join_values(values: Any, fallback: str = "待补充") -> str:
    if not isinstance(values, list):
        return fallback
    rows = [str(item).strip() for item in values if str(item).strip()]
    return "、".join(rows) if rows else fallback


def _stringify_observations(context: dict[str, Any]) -> str:
    rows = context.get("latest_observations", [])
    if not isinstance(rows, list):
        return "待补充"
    parts: list[str] = []
    for item in rows[:8]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        value = str(item.get("value") or "").strip()
        flag = str(item.get("abnormal_flag") or "").strip()
        if not name or not value:
            continue
        parts.append(f"{name}：{value}{f'（{flag}）' if flag else ''}")
    return "；".join(parts) if parts else "待补充"


def _extract_observation_value(context: dict[str, Any], aliases: list[str]) -> str:
    rows = context.get("latest_observations", [])
    if not isinstance(rows, list):
        return ""

    normalized_aliases = {alias.strip().lower() for alias in aliases if alias.strip()}
    for item in rows:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip().lower()
        value = str(item.get("value") or "").strip()
        if not name or not value:
            continue
        if name in normalized_aliases or any(alias in name for alias in normalized_aliases):
            return value
    return ""


def _normalize_measurement_value(field_key: str, value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""

    compact = (
        text.replace("\uFF05", "%")
        .replace("\uFF0F", "/")
        .replace("\u2103", "\u00B0C")
        .replace("\u3000", " ")
    ).strip()

    if field_key == "temperature_value":
        compact = re.sub(r"\s*(?:\u00B0C|\u00B0c|\u2103)$", "", compact, flags=re.IGNORECASE)
    elif field_key in {"pulse_value", "heart_rate_value", "respiratory_rate"}:
        compact = re.sub(r"\s*(?:\u6B21/\u5206|bpm)$", "", compact, flags=re.IGNORECASE)
    elif field_key == "spo2_value":
        compact = re.sub(r"\s*%$", "", compact)
    elif field_key == "blood_pressure":
        compact = re.sub(r"\s*mmhg$", "", compact, flags=re.IGNORECASE)
    elif field_key == "blood_glucose_value":
        compact = re.sub(r"\s*mmol/l$", "", compact, flags=re.IGNORECASE)

    return compact.strip()


def _base_field_values(context: dict[str, Any], spoken_text: str, document_type: str) -> dict[str, str]:
    normalized_type = normalize_document_type(document_type)
    requested_by = _safe_string(context.get("requested_by"), "待签名")
    blood_pressure = _safe_string(
        context.get("blood_pressure") or _extract_observation_value(context, ["血压", "bp"]),
        "待补充",
    )
    temperature_value = _safe_string(
        context.get("temperature_value") or _extract_observation_value(context, ["体温", "t"]),
        "待补充",
    )
    pulse_value = _safe_string(
        context.get("pulse_value") or _extract_observation_value(context, ["脉搏", "pulse", "心率", "hr"]),
        "待补充",
    )
    heart_rate_value = _safe_string(
        context.get("heart_rate_value")
        or context.get("pulse_value")
        or _extract_observation_value(context, ["心率", "hr", "脉搏", "pulse"]),
        "待补充",
    )
    respiratory_rate = _safe_string(
        context.get("respiratory_rate") or _extract_observation_value(context, ["呼吸", "呼吸频率", "r"]),
        "待补充",
    )
    spo2_value = _safe_string(
        context.get("spo2_value") or _extract_observation_value(context, ["spo2", "血氧饱和度", "血氧"]),
        "待补充",
    )
    blood_glucose_value = _safe_string(
        context.get("blood_glucose_value")
        or context.get("glucose_value")
        or _extract_observation_value(context, ["血糖", "随机血糖", "末梢血糖"]),
        "待补充",
    )
    pain_score = _safe_string(
        context.get("pain_score") or _extract_observation_value(context, ["疼痛评分", "疼痛"]),
        "待补充",
    )
    blood_pressure = _safe_string(
        _normalize_measurement_value(
            "blood_pressure",
            context.get("blood_pressure") or _extract_observation_value(context, ["\u8840\u538B", "bp"]),
        )
    )
    temperature_value = _safe_string(
        _normalize_measurement_value(
            "temperature_value",
            context.get("temperature_value") or _extract_observation_value(context, ["\u4F53\u6E29", "t"]),
        )
    )
    pulse_value = _safe_string(
        _normalize_measurement_value(
            "pulse_value",
            context.get("pulse_value")
            or _extract_observation_value(context, ["\u8109\u640F", "pulse", "\u5FC3\u7387", "hr"]),
        )
    )
    heart_rate_value = _safe_string(
        _normalize_measurement_value(
            "heart_rate_value",
            context.get("heart_rate_value")
            or context.get("pulse_value")
            or _extract_observation_value(context, ["\u5FC3\u7387", "hr", "\u8109\u640F", "pulse"]),
        )
    )
    respiratory_rate = _safe_string(
        _normalize_measurement_value(
            "respiratory_rate",
            context.get("respiratory_rate")
            or _extract_observation_value(context, ["\u547C\u5438", "\u547C\u5438\u9891\u7387", "r"]),
        )
    )
    spo2_value = _safe_string(
        _normalize_measurement_value(
            "spo2_value",
            context.get("spo2_value")
            or _extract_observation_value(context, ["spo2", "\u8840\u6C27\u9971\u548C\u5EA6", "\u8840\u6C27"]),
        )
    )
    blood_glucose_value = _safe_string(
        _normalize_measurement_value(
            "blood_glucose_value",
            context.get("blood_glucose_value")
            or context.get("glucose_value")
            or _extract_observation_value(
                context,
                ["\u8840\u7CD6", "\u968F\u673A\u8840\u7CD6", "\u672B\u68A2\u8840\u7CD6"],
            ),
        )
    )
    pain_score = _safe_string(
        _normalize_measurement_value(
            "pain_score",
            context.get("pain_score") or _extract_observation_value(context, ["\u75BC\u75DB\u8BC4\u5206", "\u75BC\u75DB"]),
        )
    )
    return {
        "document_type": normalized_type,
        "hospital_name": _safe_string(context.get("hospital_name"), "待补充"),
        "patient_id": _safe_string(context.get("patient_id"), "-"),
        "patient_name": _safe_string(context.get("patient_name") or context.get("full_name"), "-"),
        "full_name": _safe_string(context.get("full_name") or context.get("patient_name"), "-"),
        "gender": _safe_string(context.get("gender")),
        "age": _safe_string(context.get("age")),
        "department_name": _safe_string(context.get("department_name")),
        "ward_name": _safe_string(context.get("ward_name")),
        "bed_no": _safe_string(context.get("bed_no"), "-"),
        "mrn": _safe_string(context.get("mrn"), "-"),
        "inpatient_no": _safe_string(context.get("inpatient_no"), "-"),
        "chart_date": _safe_string(context.get("chart_date")),
        "shift_date": _safe_string(context.get("shift_date")),
        "shift_type": _safe_string(context.get("shift_type")),
        "current_time": _safe_string(context.get("current_time")),
        "admission_date": _safe_string(context.get("admission_date")),
        "patient_identifier": _safe_string(context.get("patient_identifier") or context.get("patient_id"), "-"),
        "education_level": _safe_string(context.get("education_level")),
        "phone": _safe_string(context.get("phone")),
        "ethnicity": _safe_string(context.get("ethnicity")),
        "blood_type": _safe_string(context.get("blood_type")),
        "rh_type": _safe_string(context.get("rh_type")),
        "consciousness": _safe_string(context.get("consciousness")),
        "temperature_value": temperature_value,
        "pulse_value": pulse_value,
        "heart_rate_value": heart_rate_value,
        "respiratory_rate": respiratory_rate,
        "blood_pressure": blood_pressure,
        "blood_pressure_morning": _safe_string(context.get("blood_pressure_morning") or blood_pressure),
        "blood_pressure_noon": _safe_string(context.get("blood_pressure_noon")),
        "blood_pressure_afternoon": _safe_string(context.get("blood_pressure_afternoon")),
        "blood_pressure_night": _safe_string(context.get("blood_pressure_night")),
        "spo2_value": spo2_value,
        "cvp_value": _safe_string(context.get("cvp_value")),
        "blood_glucose_value": blood_glucose_value,
        "pain_score": pain_score,
        "height": _safe_string(context.get("height")),
        "weight": _safe_string(context.get("weight")),
        "urine_volume": _safe_string(context.get("urine_volume")),
        "intake_total": _safe_string(context.get("intake_total")),
        "output_total": _safe_string(context.get("output_total")),
        "stool_count": _safe_string(context.get("stool_count")),
        "intake_summary": _safe_string(context.get("intake_summary") or context.get("intake_total")),
        "output_summary": _safe_string(context.get("output_summary") or context.get("output_total")),
        "diagnoses": _join_values(context.get("diagnoses"), "待补充"),
        "risk_level": _safe_string(context.get("risk_level"), "待评估"),
        "risk_tags": _join_values(context.get("risk_tags"), "暂无"),
        "pending_tasks": _join_values(context.get("pending_tasks"), "暂无"),
        "observation_summary": _stringify_observations(context),
        "spoken_text": spoken_text or "待补充",
        "special_notes": _safe_string(context.get("special_notes") or spoken_text),
        "requested_by": requested_by,
        "supervisor_sign": _safe_string(context.get("supervisor_sign")),
        "hospital_day": _safe_string(context.get("hospital_day")),
        "post_op_day": _safe_string(context.get("post_op_day")),
        "operation_name": _safe_string(context.get("operation_name")),
        "preop_preparation": _safe_string(context.get("preop_preparation")),
        "venous_access": _safe_string(context.get("venous_access")),
        "catheter_status": _safe_string(context.get("catheter_status")),
        "drug_allergy": _safe_string(context.get("drug_allergy") or context.get("allergy_info")),
        "puncture_site": _safe_string(context.get("puncture_site")),
        "position_fixation": _safe_string(context.get("position_fixation")),
        "electrotome_status": _safe_string(context.get("electrotome_status")),
        "specimen_status": _safe_string(context.get("specimen_status")),
        "special_record": _safe_string(context.get("special_record") or spoken_text),
        "instrument_count": _safe_string(context.get("instrument_count")),
        "dressing_count": _safe_string(context.get("dressing_count")),
        "special_item_count": _safe_string(context.get("special_item_count")),
        "integrity_check": _safe_string(context.get("integrity_check")),
        "operation_end_status": _safe_string(context.get("operation_end_status")),
        "drainage_status": _safe_string(context.get("drainage_status")),
        "handover_items": _safe_string(context.get("handover_items")),
        "transfusion_reaction_history": _safe_string(context.get("transfusion_reaction_history")),
        "precheck_summary": _safe_string(context.get("precheck_summary")),
        "double_checker": _safe_string(context.get("double_checker")),
        "temperature_before": _safe_string(context.get("temperature_before")),
        "blood_pressure_before": _safe_string(context.get("blood_pressure_before")),
        "transfusion_start_time": _safe_string(context.get("transfusion_start_time")),
        "transfusion_end_time": _safe_string(context.get("transfusion_end_time")),
        "temperature_after": _safe_string(context.get("temperature_after")),
        "blood_pressure_after": _safe_string(context.get("blood_pressure_after")),
        "actual_transfusion_volume": _safe_string(context.get("actual_transfusion_volume")),
        "transfusion_reaction": _safe_string(context.get("transfusion_reaction")),
        "transfusion_priority": _safe_string(context.get("transfusion_priority")),
        "donor_barcode": _safe_string(context.get("donor_barcode")),
        "blood_component": _safe_string(context.get("blood_component")),
        "blood_volume": _safe_string(context.get("blood_volume")),
        "first_stage_rate": _safe_string(context.get("first_stage_rate")),
        "first_stage_reaction": _safe_string(context.get("first_stage_reaction")),
        "second_stage_rate": _safe_string(context.get("second_stage_rate")),
        "second_stage_reaction": _safe_string(context.get("second_stage_reaction")),
        "glucose_value": _safe_string(context.get("glucose_value")),
        "breakfast_before_glucose": _safe_string(context.get("breakfast_before_glucose")),
        "breakfast_after_glucose": _safe_string(context.get("breakfast_after_glucose")),
        "lunch_before_glucose": _safe_string(context.get("lunch_before_glucose")),
        "lunch_after_glucose": _safe_string(context.get("lunch_after_glucose")),
        "dinner_before_glucose": _safe_string(context.get("dinner_before_glucose")),
        "dinner_after_glucose": _safe_string(context.get("dinner_after_glucose")),
        "bedtime_glucose": _safe_string(context.get("bedtime_glucose")),
        "special_disease_care": _safe_string(context.get("special_disease_care")),
        "other_summary": _safe_string(context.get("other_summary")),
        "infectious_report": _safe_string(context.get("infectious_report")),
        "ward_total": _safe_string(context.get("ward_total")),
        "discharge_count": _safe_string(context.get("discharge_count")),
        "transfer_out_count": _safe_string(context.get("transfer_out_count")),
        "admission_count": _safe_string(context.get("admission_count")),
        "transfer_in_count": _safe_string(context.get("transfer_in_count")),
        "operation_count": _safe_string(context.get("operation_count")),
        "tomorrow_operation_count": _safe_string(context.get("tomorrow_operation_count")),
        "serious_count": _safe_string(context.get("serious_count")),
        "critical_count": _safe_string(context.get("critical_count")),
        "death_count": _safe_string(context.get("death_count")),
        "special_events": _safe_string(context.get("special_events")),
        "receiver_sign": _safe_string(context.get("receiver_sign")),
        "yang_pattern": _safe_string(context.get("yang_pattern")),
        "yin_pattern": _safe_string(context.get("yin_pattern")),
        "collapse_pattern": _safe_string(context.get("collapse_pattern")),
        "symptom_name": _safe_string(context.get("symptom_name")),
        "nursing_method": _safe_string(context.get("nursing_method")),
        "tcm_technique": _safe_string(context.get("tcm_technique")),
        "severity_before_date": _safe_string(context.get("severity_before_date")),
        "severity_before_score": _safe_string(context.get("severity_before_score")),
        "severity_after_date": _safe_string(context.get("severity_after_date")),
        "severity_after_score": _safe_string(context.get("severity_after_score")),
        "effect_evaluation": _safe_string(context.get("effect_evaluation")),
    }


def _build_editable_blocks(document_type: str, field_values: dict[str, str]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for field in get_field_schema(document_type):
        key = str(field.get("key") or "").strip()
        value = str(field_values.get(key) or "").strip()
        blocks.append(
            {
                "key": key,
                "label": str(field.get("label") or key),
                "section": str(field.get("section") or "文书内容"),
                "value": value,
                "required": bool(field.get("required")),
                "editable": True,
                "status": "missing" if value in MISSING_VALUE_TOKENS else "filled",
                "input_type": str(field.get("input_type") or "text"),
                "placeholder": "请填写" if bool(field.get("required")) else "可补充",
            }
        )
    return blocks


def _build_sections(editable_blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for block in editable_blocks:
        grouped.setdefault(str(block["section"]), []).append(block)

    return [
        {
            "title": title,
            "field_count": len(items),
            "missing_count": sum(1 for item in items if item["status"] == "missing"),
            "field_keys": [str(item["key"]) for item in items],
        }
        for title, items in grouped.items()
    ]


def _build_structured_payload(
    *,
    document_type: str,
    template_name: str | None,
    render_mode: str,
    draft_text: str,
    field_values: dict[str, str],
) -> dict[str, Any]:
    normalized_type = normalize_document_type(document_type)
    editable_blocks = _build_editable_blocks(normalized_type, field_values)
    missing_fields = [
        {"key": item["key"], "label": item["label"], "section": item["section"]}
        for item in editable_blocks
        if item["status"] == "missing"
    ]
    standard_form = get_standard_form_definition(normalized_type)
    return {
        "template_name": template_name or standard_form["name"],
        "template_applied": True,
        "render_mode": render_mode,
        "standardized_format": True,
        "editable": True,
        "document_type": normalized_type,
        "editable_blocks": editable_blocks,
        "sections": _build_sections(editable_blocks),
        "missing_fields": missing_fields,
        "manual_review_required": True,
        "field_summary": {
            "total": len(editable_blocks),
            "filled": len(editable_blocks) - len(missing_fields),
            "missing": len(missing_fields),
        },
        "draft_outline": [line.strip() for line in draft_text.splitlines() if line.strip()][:20],
        "standard_form": {
            "id": standard_form["id"],
            "name": standard_form["name"],
            "standard_family": standard_form.get("standard_family"),
            "description": standard_form.get("description"),
            "schema_version": standard_form.get("schema_version"),
            "source_refs": list(standard_form.get("source_refs") or []),
            "sections": list(standard_form.get("sections") or []),
            "field_count": len(editable_blocks),
            "sheet_columns": get_field_schema(normalized_type),
            "questionnaire": build_standard_form_questionnaire(normalized_type),
        },
    }


def hydrate_legacy_structured_fields(
    *,
    document_type: str,
    context: dict[str, Any],
    draft_text: str,
    spoken_text: str | None = None,
    template_name: str | None = None,
    existing_fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_type = normalize_document_type(document_type)
    existing = dict(existing_fields or {})
    field_values = _base_field_values(context, spoken_text or "", normalized_type)

    for key, value in existing.items():
        if isinstance(value, list):
            merged = _join_values(value, "")
            if merged:
                field_values[key] = merged
        elif value is not None:
            text = str(value).strip()
            if text and text not in MISSING_VALUE_TOKENS:
                field_values[key] = text

    hydrated = _build_structured_payload(
        document_type=normalized_type,
        template_name=template_name or str(existing.get("template_name") or "").strip() or None,
        render_mode="legacy_hydrated",
        draft_text=draft_text,
        field_values=field_values,
    )
    return {
        **existing,
        **hydrated,
        "editable": True,
        "standardized_format": True,
        "manual_review_required": True,
    }


def _ensure_review_notice(draft_text: str) -> str:
    text = draft_text.strip()
    if MANUAL_REVIEW_NOTICE in text:
        return text
    return f"{text}\n\n{MANUAL_REVIEW_NOTICE}".strip()


def _fallback_render(
    document_type: str,
    template_text: str,
    context: dict[str, Any],
    spoken_text: str,
    template_name: str | None,
) -> dict[str, Any]:
    normalized_type = normalize_document_type(document_type)
    field_values = _base_field_values(context, spoken_text, normalized_type)
    rendered = template_text
    for key, value in field_values.items():
        rendered = rendered.replace(f"{{{{{key}}}}}", value)
    rendered = re.sub(r"\{\{\s*[^{}]+\s*\}\}", "", rendered)
    rendered = re.sub(r"\{[A-Za-z0-9_]+\}", "", rendered)
    rendered = re.sub(r"[ \t]+\n", "\n", rendered)
    rendered = re.sub(r"\n{3,}", "\n\n", rendered).strip()
    rendered = _ensure_review_notice(rendered.replace("{{", "").replace("}}", ""))
    return {
        "draft_text": rendered,
        "structured_fields": _build_structured_payload(
            document_type=normalized_type,
            template_name=template_name,
            render_mode="fallback",
            draft_text=rendered,
            field_values=field_values,
        ),
    }


async def _openai_compatible_chat(
    *,
    base_url: str,
    model: str,
    prompt: dict[str, Any],
    api_key: str = "",
    timeout_sec: int = 30,
) -> str | None:
    endpoint = f"{base_url.rstrip('/')}/chat/completions"
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你是临床护理文书助手。必须只返回 JSON 对象，不要输出解释。"},
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
        ],
        "temperature": 0.2,
    }
    try:
        async with httpx.AsyncClient(timeout=timeout_sec, trust_env=False) as client:
            response = await client.post(endpoint, headers=headers, json=payload)
            response.raise_for_status()
            body = response.json()
    except Exception:
        return None

    content = body.get("choices", [{}])[0].get("message", {}).get("content", "")
    text = str(content).strip()
    return text or None


async def _probe_local_model_ids(*, base_url: str, api_key: str = "", timeout_sec: int = 2) -> set[str]:
    now = time.monotonic()
    cached = _local_model_probe_cache.get("models")
    if isinstance(cached, set) and (now - float(_local_model_probe_cache.get("ts") or 0.0)) <= LOCAL_MODEL_CACHE_TTL_SEC:
        return set(cached)

    endpoint = f"{base_url.rstrip('/')}/models"
    headers: dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        async with httpx.AsyncClient(timeout=timeout_sec, trust_env=False) as client:
            response = await client.get(endpoint, headers=headers)
            response.raise_for_status()
            body = response.json()
    except Exception:
        return set(cached or [])

    models = body.get("data", []) if isinstance(body, dict) else []
    out: set[str] = set()
    for item in models:
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("id") or item.get("model") or "").strip()
        if model_id:
            out.add(model_id)

    _local_model_probe_cache["ts"] = now
    _local_model_probe_cache["models"] = set(out)
    return out


def _parse_draft_json(content: str | None) -> tuple[str, dict[str, Any]] | None:
    if not content:
        return None
    try:
        parsed = json.loads(content)
    except Exception:
        return None
    if not isinstance(parsed, dict):
        return None
    draft_text = str(parsed.get("draft_text") or parsed.get("draft") or "").strip()
    if not draft_text:
        return None
    structured_fields = parsed.get("structured_fields", {})
    return draft_text, structured_fields if isinstance(structured_fields, dict) else {}


async def adapt_document_by_template(
    *,
    document_type: str,
    template_text: str,
    template_name: str | None,
    spoken_text: str,
    context: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    normalized_type = normalize_document_type(document_type)
    field_values = _base_field_values(context, spoken_text, normalized_type)
    standard_form = get_standard_form_definition(normalized_type)

    if settings.mock_mode and not settings.llm_force_enable:
        fallback = _fallback_render(normalized_type, template_text, context, spoken_text, template_name)
        return fallback["draft_text"], fallback["structured_fields"]

    prompt = {
        "task": "护理文书模板自适应填写",
        "document_type": normalized_type,
        "template_name": template_name or standard_form["name"],
        "template_text": template_text,
        "spoken_text": spoken_text,
        "patient_context": {
            "patient_id": context.get("patient_id"),
            "patient_name": context.get("patient_name") or context.get("full_name"),
            "bed_no": context.get("bed_no"),
            "mrn": context.get("mrn"),
            "inpatient_no": context.get("inpatient_no"),
            "gender": context.get("gender"),
            "age": context.get("age"),
            "blood_type": context.get("blood_type"),
            "diagnoses": context.get("diagnoses", []),
            "risk_tags": context.get("risk_tags", []),
            "pending_tasks": context.get("pending_tasks", []),
            "latest_observations": context.get("latest_observations", []),
            "risk_level": context.get("risk_level"),
        },
        "standard_form": {
            "name": standard_form["name"],
            "sections": standard_form.get("sections", []),
            "fields": get_field_schema(normalized_type),
        },
        "constraints": {
            "language": "zh-CN",
            "must_keep_template_structure": True,
            "must_fill_only_supported_fields": True,
            "must_append_manual_review_notice": True,
            "output_json_only": True,
        },
        "output_schema": {
            "draft_text": "string",
            "structured_fields": "object",
        },
    }

    local_models = [settings.local_llm_model_primary, settings.local_llm_model_fallback] if settings.local_llm_enabled else []
    local_models = [item for item in local_models if item]
    if local_models:
        online_models = await _probe_local_model_ids(base_url=settings.local_llm_base_url, api_key=settings.local_llm_api_key)
        if online_models:
            matched = [item for item in local_models if item in online_models]
            local_models = matched if matched else list(online_models)[:2]

    per_model_timeout = (
        min(DOC_LOCAL_RENDER_TIMEOUT_SEC, max(6, int(settings.local_llm_timeout_sec / max(1, len(local_models)))))
        if local_models
        else DOC_LOCAL_RENDER_TIMEOUT_SEC
    )

    for model in local_models:
        content = await _openai_compatible_chat(
            base_url=settings.local_llm_base_url,
            model=model,
            prompt=prompt,
            api_key=settings.local_llm_api_key,
            timeout_sec=per_model_timeout,
        )
        parsed = _parse_draft_json(content)
        if not parsed:
            continue
        draft_text, structured_fields = parsed
        normalized = _build_structured_payload(
            document_type=normalized_type,
            template_name=template_name,
            render_mode="local_llm",
            draft_text=_ensure_review_notice(draft_text),
            field_values=field_values,
        )
        normalized.update(structured_fields)
        normalized.setdefault("template_name", template_name or standard_form["name"])
        normalized.setdefault("template_applied", True)
        normalized.setdefault("render_mode", "local_llm")
        normalized.setdefault("standardized_format", True)
        normalized.setdefault("editable", True)
        normalized.setdefault("standard_form", _build_structured_payload(
            document_type=normalized_type,
            template_name=template_name,
            render_mode="local_llm",
            draft_text=draft_text,
            field_values=field_values,
        )["standard_form"])
        return _ensure_review_notice(draft_text), normalized

    if not settings.local_only_mode and settings.bailian_api_key:
        content = await _openai_compatible_chat(
            base_url=settings.bailian_base_url,
            model=settings.bailian_model_default,
            prompt=prompt,
            api_key=settings.bailian_api_key,
            timeout_sec=40,
        )
        parsed = _parse_draft_json(content)
        if parsed:
            draft_text, structured_fields = parsed
            normalized = _build_structured_payload(
                document_type=normalized_type,
                template_name=template_name,
                render_mode="llm",
                draft_text=_ensure_review_notice(draft_text),
                field_values=field_values,
            )
            normalized.update(structured_fields)
            normalized.setdefault("template_name", template_name or standard_form["name"])
            normalized.setdefault("template_applied", True)
            normalized.setdefault("render_mode", "llm")
            normalized.setdefault("standardized_format", True)
            normalized.setdefault("editable", True)
            return _ensure_review_notice(draft_text), normalized

    fallback = _fallback_render(normalized_type, template_text, context, spoken_text, template_name)
    fallback_structured = dict(fallback["structured_fields"])
    fallback_structured["llm_status"] = "fallback_rendered"
    return fallback["draft_text"], fallback_structured
