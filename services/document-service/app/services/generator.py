from __future__ import annotations

from typing import Any

from app.services.llm_client import adapt_document_by_template
from app.services.standard_forms import normalize_document_type
from app.services.store import document_store


CRITICAL_PATIENT_TEMPLATE_TEXT = (
    "【病重（病危）患者护理记录单】\n"
    "科别：{{department_name}}  姓名：{{patient_name}}  床号：{{bed_no}}  病案号：{{mrn}}\n"
    "记录时间：{{current_time}}\n"
    "主要诊断：{{diagnoses}}\n"
    "体温：{{temperature_value}}℃  心率/脉搏：{{pulse_value}}次/分  呼吸：{{respiratory_rate}}次/分  "
    "SpO2：{{spo2_value}}%  血压：{{blood_pressure}}mmHg\n"
    "入量：{{intake_total}}  出量：{{output_total}}\n"
    "病情观察：{{observation_summary}}\n"
    "风险等级：{{risk_level}}  重点风险：{{risk_tags}}\n"
    "护理措施与效果：{{spoken_text}}\n"
    "下一班观察重点：{{pending_tasks}}\n"
    "护士签名：{{requested_by}}\n"
)


def _sanitize_pending_tasks(items: Any) -> list[str]:
    if not isinstance(items, list):
        return []
    cleaned: list[str] = []
    for item in items:
        text = str(item or "").strip()
        if not text:
            continue
        if text.startswith("文书状态：") or text.startswith("最新文书："):
            continue
        if text not in cleaned:
            cleaned.append(text)
    return cleaned


async def build_document_draft(
    *,
    document_type: str,
    spoken_text: str | None,
    context: dict[str, Any],
    template_text: str | None = None,
    template_name: str | None = None,
) -> tuple[str, dict[str, Any]]:
    normalized_type = normalize_document_type(document_type)
    cleaned_pending_tasks = _sanitize_pending_tasks(context.get("pending_tasks"))
    context = {
        **context,
        "patient_name": context.get("patient_name") or context.get("full_name"),
        "full_name": context.get("full_name") or context.get("patient_name"),
        "requested_by": context.get("requested_by"),
        "pending_tasks": cleaned_pending_tasks,
    }
    preferred_template = None
    resolved_template_text = template_text
    resolved_template_name = template_name
    diagnoses = "、".join(context.get("diagnoses", [])) or "待补充"
    risk_tags = "、".join(context.get("risk_tags", [])) or "暂无"
    pending_tasks = "、".join(cleaned_pending_tasks) or "暂无"
    spoken = spoken_text or "患者病情平稳，继续监测。"

    if not resolved_template_text:
        preferred_template = document_store.get_preferred_template(document_type)
        if preferred_template is not None:
            resolved_template_text = preferred_template.template_text
            resolved_template_name = preferred_template.name

    template_locked = bool(preferred_template and preferred_template.source_type == "system")
    if normalized_type == "critical_patient_nursing_record" and template_locked:
        resolved_template_text = CRITICAL_PATIENT_TEMPLATE_TEXT
        resolved_template_name = "病重（病危）患者护理记录单"

    if resolved_template_text:
        draft_text, template_structured = await adapt_document_by_template(
            document_type=normalized_type,
            template_text=resolved_template_text,
            template_name=resolved_template_name,
            spoken_text=spoken,
            context=context,
        )
        structured_fields = {
            "diagnoses": context.get("diagnoses", []),
            "risk_tags": context.get("risk_tags", []),
            "pending_tasks": cleaned_pending_tasks,
            "spoken_text": spoken_text,
            "template_name": resolved_template_name,
            "template_applied": True,
            "template_locked": template_locked,
            "template_source_policy": "system_standard_locked" if template_locked else "preferred_template",
            "template_source_refs": list(preferred_template.source_refs or []) if preferred_template else [],
        }
        structured_fields.update(template_structured)
        return draft_text, structured_fields

    draft_text = (
        f"[{normalized_type}]\n"
        f"患者ID: {context.get('patient_id')} 床号: {context.get('bed_no', '-')}\n"
        f"主要诊断: {diagnoses}\n"
        f"风险标签: {risk_tags}\n"
        f"待处理任务: {pending_tasks}\n"
        f"护理记录: {spoken}\n"
        "AI提示: 该草稿需人工复核后提交。"
    )

    structured_fields = {
        "diagnoses": context.get("diagnoses", []),
        "risk_tags": context.get("risk_tags", []),
        "pending_tasks": cleaned_pending_tasks,
        "spoken_text": spoken_text,
        "template_name": template_name,
        "template_applied": False,
    }
    return draft_text, structured_fields
