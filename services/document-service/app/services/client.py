from __future__ import annotations

import logging
from typing import Any

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

PATIENT_CONTEXT_TIMEOUT = httpx.Timeout(3.5, connect=1.0)
AUDIT_TIMEOUT = httpx.Timeout(5.0, connect=2.0)


async def fetch_patient_context(patient_id: str) -> dict[str, Any] | None:
    should_local_mock = settings.mock_mode and not settings.llm_force_enable
    if should_local_mock:
        return {
            "patient_id": patient_id,
            "patient_name": "张晓明",
            "bed_no": "12",
            "encounter_id": "enc-001",
            "mrn": "MRN-0001",
            "inpatient_no": "IP-2026-0001",
            "gender": "男",
            "age": 45,
            "blood_type": "A+",
            "allergy_info": "青霉素过敏",
            "diagnoses": ["慢性心衰急性加重"],
            "risk_tags": ["低血压风险", "液体管理风险"],
            "pending_tasks": ["复测血压", "记录尿量"],
            "latest_observations": [
                {"name": "收缩压", "value": "88 mmHg", "abnormal_flag": "low"},
                {"name": "4小时尿量", "value": "85 ml", "abnormal_flag": "low"},
            ],
        }

    try:
        async with httpx.AsyncClient(timeout=PATIENT_CONTEXT_TIMEOUT, trust_env=False) as client:
            context_resp = await client.get(f"{settings.patient_context_service_url}/patients/{patient_id}/context")
            if context_resp.status_code >= 400:
                return None
            context = context_resp.json()
            if not isinstance(context, dict):
                return None
            try:
                patient_resp = await client.get(f"{settings.patient_context_service_url}/patients/{patient_id}")
            except httpx.HTTPError as exc:
                logger.warning("document_service_patient_detail_fetch_failed patient_id=%s error=%s", patient_id, exc)
                return context
    except httpx.HTTPError as exc:
        logger.warning("document_service_patient_context_fetch_failed patient_id=%s error=%r", patient_id, exc)
        return None
    except Exception as exc:
        logger.warning("document_service_patient_context_unexpected_error patient_id=%s error=%r", patient_id, exc)
        return None

    if patient_resp.status_code < 400:
        try:
            patient = patient_resp.json()
        except Exception:
            patient = None
        if isinstance(patient, dict):
            context.setdefault("patient_name", patient.get("full_name"))
            context.setdefault("full_name", patient.get("full_name"))
            context.setdefault("mrn", patient.get("mrn"))
            context.setdefault("inpatient_no", patient.get("inpatient_no"))
            context.setdefault("gender", patient.get("gender"))
            context.setdefault("age", patient.get("age"))
            context.setdefault("blood_type", patient.get("blood_type"))
            context.setdefault("allergy_info", patient.get("allergy_info"))
    return context


async def fetch_bed_context(
    bed_no: str,
    *,
    department_id: str | None = None,
    requested_by: str | None = None,
) -> dict[str, Any] | None:
    target_bed_no = str(bed_no or "").strip()
    if not target_bed_no:
        return None

    should_local_mock = settings.mock_mode and not settings.llm_force_enable
    if should_local_mock:
        return {
            "patient_id": "mock-patient-001",
            "patient_name": "张晓明",
            "full_name": "张晓明",
            "bed_no": target_bed_no,
            "encounter_id": "enc-001",
            "mrn": "MRN-0001",
            "inpatient_no": "IP-2026-0001",
            "gender": "男",
            "age": 45,
            "blood_type": "A+",
            "allergy_info": "青霉素过敏",
            "diagnoses": ["慢性心衰急性加重"],
            "risk_tags": ["低血压风险", "液体管理风险"],
            "pending_tasks": ["复测血压", "记录尿量"],
            "latest_observations": [
                {"name": "收缩压", "value": "88 mmHg", "abnormal_flag": "low"},
                {"name": "4小时尿量", "value": "85 ml", "abnormal_flag": "low"},
            ],
        }

    params: dict[str, Any] = {}
    if department_id:
        params["department_id"] = department_id
    if requested_by:
        params["requested_by"] = requested_by

    try:
        async with httpx.AsyncClient(timeout=PATIENT_CONTEXT_TIMEOUT, trust_env=False) as client:
            response = await client.get(
                f"{settings.patient_context_service_url}/beds/{target_bed_no}/context",
                params=params or None,
            )
    except httpx.HTTPError as exc:
        logger.warning("document_service_bed_context_fetch_failed bed_no=%s error=%r", target_bed_no, exc)
        return None
    except Exception as exc:
        logger.warning("document_service_bed_context_unexpected_error bed_no=%s error=%r", target_bed_no, exc)
        return None

    if response.status_code >= 400:
        return None

    try:
        payload = response.json()
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


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
    async with httpx.AsyncClient(timeout=AUDIT_TIMEOUT, trust_env=False) as client:
        try:
            await client.post(f"{settings.audit_service_url}/audit/log", json=payload)
        except Exception:
            return
