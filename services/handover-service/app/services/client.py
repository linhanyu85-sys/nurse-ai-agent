from __future__ import annotations

from typing import Any

import httpx

from app.core.config import settings


async def fetch_patient_context(patient_id: str) -> dict[str, Any] | None:
    async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
        try:
            context_resp = await client.get(f"{settings.patient_context_service_url}/patients/{patient_id}/context")
            if context_resp.status_code >= 400:
                raise RuntimeError("patient_context_not_found")
            patient_resp = await client.get(f"{settings.patient_context_service_url}/patients/{patient_id}")
        except Exception:
            if not settings.mock_mode:
                return None
            return {
                "patient_id": patient_id,
                "patient_name": "张晓明",
                "bed_no": "12",
                "encounter_id": "enc-001",
                "mrn": "MRN-0001",
                "diagnoses": ["慢性心衰急性加重"],
                "risk_tags": ["低血压风险", "液体管理风险"],
                "pending_tasks": ["复测血压", "记录尿量"],
                "latest_observations": [
                    {"name": "收缩压", "value": "88 mmHg", "abnormal_flag": "low"},
                    {"name": "4小时尿量", "value": "85 ml", "abnormal_flag": "low"},
                ],
            }

    context = context_resp.json()
    if not isinstance(context, dict):
        return None
    if patient_resp.status_code < 400:
        patient = patient_resp.json()
        if isinstance(patient, dict):
            context.setdefault("patient_name", patient.get("full_name"))
            context.setdefault("mrn", patient.get("mrn"))
            context.setdefault("gender", patient.get("gender"))
            context.setdefault("age", patient.get("age"))
    return context


async def fetch_ward_beds(department_id: str) -> list[dict[str, Any]]:
    url = f"{settings.patient_context_service_url}/wards/{department_id}/beds"
    async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
        try:
            response = await client.get(url)
        except Exception:
            response = None
    if response is not None and response.status_code < 400:
        data = response.json()
        return data if isinstance(data, list) else []
    if settings.mock_mode:
        return [
            {"bed_no": "12", "current_patient_id": "pat-001"},
            {"bed_no": "15", "current_patient_id": "pat-002"},
        ]
    return []


async def write_audit_log(
    action: str,
    resource_type: str,
    resource_id: str | None,
    detail: dict[str, Any],
    user_id: str | None = None,
) -> None:
    payload = {
        "user_id": user_id,
        "action": action,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "detail": detail,
    }
    async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
        try:
            await client.post(f"{settings.audit_service_url}/audit/log", json=payload)
        except Exception:
            return
