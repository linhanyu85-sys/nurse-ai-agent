from __future__ import annotations

from datetime import date
from typing import Any

from app.schemas.handover import HandoverRecord
from app.services.store import handover_store


def build_handover_from_context(
    *,
    patient_id: str,
    context: dict[str, Any],
    shift_date: date,
    shift_type: str,
    generated_by: str | None = None,
) -> HandoverRecord:
    risk_tags = context.get("risk_tags", [])
    pending_tasks = context.get("pending_tasks", [])
    observations = context.get("latest_observations", [])
    diagnoses = context.get("diagnoses", [])
    patient_name = str(context.get("patient_name") or context.get("full_name") or "").strip()
    bed_no = str(context.get("bed_no") or "").strip()
    patient_label = f"{bed_no}床{patient_name}".strip()
    if not patient_label:
        patient_label = patient_id

    observation_lines: list[str] = []
    for item in observations[:4]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        value = str(item.get("value") or "").strip()
        if not name or not value:
            continue
        observation_lines.append(f"{name}{value}")

    summary_parts = [
        f"{patient_label}交班",
        f"诊断：{('、'.join(diagnoses) if diagnoses else '待补充')}",
        f"重点风险：{('、'.join(risk_tags[:3]) if risk_tags else '暂无')}",
        f"本班主要变化：{('；'.join(observation_lines) if observation_lines else '暂无显著变化')}",
        f"下一班优先：{('、'.join(pending_tasks[:3]) if pending_tasks else '继续观察并复核生命体征')}",
    ]
    summary = "。".join(summary_parts) + "。"

    new_changes = [{"type": "observation", "value": item} for item in observations[:3]]
    worsening_points = [f"{tag} 需持续监测并交代下一班" for tag in risk_tags[:3]]
    improved_points: list[str] = []
    pending_closures = pending_tasks
    next_shift_priorities = pending_tasks[:4] or ["继续评估病情变化并复核生命体征"]

    return handover_store.create(
        patient_id=patient_id,
        encounter_id=context.get("encounter_id"),
        shift_date=shift_date,
        shift_type=shift_type,
        generated_by=generated_by,
        summary=summary,
        new_changes=new_changes,
        worsening_points=worsening_points,
        improved_points=improved_points,
        pending_closures=pending_closures,
        next_shift_priorities=next_shift_priorities,
    )
