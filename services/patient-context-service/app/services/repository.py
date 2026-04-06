from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import logging
from typing import Any
from uuid import UUID, uuid4

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.core.config import settings
from app.schemas.patient import (
    AdminPatientCaseBundleOut,
    AdminPatientCaseOut,
    AdminPatientCaseUpsertRequest,
    BedOverview,
    DepartmentAdminOut,
    OrderRequestCreateRequest,
    OrderListOut,
    OrderOut,
    PatientBase,
    PatientContextOut,
    WardAnalyticsOut,
    WardHotspotOut,
)
from app.services.mock_data import (
    MOCK_BEDS,
    MOCK_DEPARTMENT_ID,
    MOCK_PATIENTS,
    get_active_orders_for_patient,
    get_dynamic_beds,
    get_dynamic_context,
    get_mock_case,
    get_order_history_for_patient,
    get_order_stats,
    list_mock_cases,
    list_mock_departments,
    mark_order_checked,
    mark_order_exception,
    mark_order_executed,
    upsert_mock_case,
    create_order_request,
)
from app.services.risk_policy import evaluate_clinical_risk

logger = logging.getLogger(__name__)


class PatientContextRepository:
    def __init__(self) -> None:
        self._engine: AsyncEngine | None = None
        self._db_disabled_until: datetime | None = None

    def _db_enabled(self) -> bool:
        if settings.mock_mode:
            return False
        if self._db_disabled_until and datetime.now(timezone.utc) < self._db_disabled_until:
            return False
        return True

    def _mark_db_unavailable(self, reason: str, cooldown_sec: int = 60) -> None:
        self._db_disabled_until = datetime.now(timezone.utc) + timedelta(seconds=max(5, cooldown_sec))
        logger.warning(
            "db_unavailable_fallback cooldown_sec=%s reason=%s",
            cooldown_sec,
            reason,
        )

    @staticmethod
    def _mock_fallback_enabled() -> bool:
        return bool(settings.mock_mode or settings.db_error_fallback_to_mock)

    @staticmethod
    def _should_use_demo_fallback(department_id: str | None = None) -> bool:
        dep = str(department_id or "").strip()
        return dep == MOCK_DEPARTMENT_ID or PatientContextRepository._mock_fallback_enabled()

    @staticmethod
    def _mock_beds_by_department(department_id: str) -> list[BedOverview]:
        if department_id == MOCK_DEPARTMENT_ID:
            source_beds = get_dynamic_beds(department_id)
        else:
            source_beds = [item for item in MOCK_BEDS if item.department_id == department_id]
        return [bed.model_copy(deep=True) for bed in source_beds]

    @staticmethod
    def _mock_all_beds() -> list[BedOverview]:
        return [bed.model_copy(deep=True) for bed in MOCK_BEDS]

    @staticmethod
    def _bed_sort_key(bed_no: str | None) -> tuple[int, str]:
        raw = str(bed_no or "").strip()
        if raw.isdigit():
            return (0, f"{int(raw):03d}")
        return (1, raw)

    @staticmethod
    def _is_uuid_like(value: str | None) -> bool:
        raw = str(value or "").strip()
        if not raw:
            return False
        try:
            UUID(raw)
            return True
        except ValueError:
            return False

    @staticmethod
    def _virtual_range() -> tuple[int, int]:
        start = int(settings.virtual_bed_no_start or 1)
        end = int(settings.virtual_bed_no_end or 40)
        if start > end:
            start, end = end, start
        start = max(1, start)
        end = max(start, end)
        return start, end

    @classmethod
    def _virtual_range_contains(cls, bed_no: str) -> bool:
        if not settings.include_virtual_empty_beds:
            return False
        raw = str(bed_no or "").strip()
        if not raw.isdigit():
            return False
        value = int(raw)
        start, end = cls._virtual_range()
        return start <= value <= end

    @classmethod
    def _augment_with_virtual_beds(
        cls,
        beds: list[BedOverview],
        *,
        department_id: str,
    ) -> list[BedOverview]:
        if not settings.include_virtual_empty_beds:
            return beds
        start, end = cls._virtual_range()
        existing = {str(item.bed_no).strip() for item in beds if str(item.bed_no).strip()}
        for num in range(start, end + 1):
            bed_no = str(num)
            if bed_no in existing:
                continue
            beds.append(
                cls._apply_risk_to_bed(
                    BedOverview(
                        id=f"virtual-{department_id}-{bed_no}",
                        department_id=department_id,
                        bed_no=bed_no,
                        room_no=f"{600 + num}",
                        status="vacant",
                        current_patient_id=None,
                        patient_name=None,
                        pending_tasks=[],
                        risk_tags=[],
                    )
                )
            )
        beds.sort(key=lambda item: cls._bed_sort_key(item.bed_no))
        return beds

    @classmethod
    def _merge_bed_snapshots(cls, primary: list[BedOverview], overlay: list[BedOverview]) -> list[BedOverview]:
        merged: dict[tuple[str, str], BedOverview] = {}
        for item in primary:
            key = (str(item.department_id or "").strip(), str(item.bed_no or "").strip())
            merged[key] = item.model_copy(deep=True)
        for item in overlay:
            key = (str(item.department_id or "").strip(), str(item.bed_no or "").strip())
            merged[key] = item.model_copy(deep=True)
        return sorted(merged.values(), key=lambda item: (str(item.department_id or "").strip(), cls._bed_sort_key(item.bed_no)))

    @staticmethod
    def _mock_patient_or_none(patient_id: str) -> PatientBase | None:
        patient = MOCK_PATIENTS.get(patient_id)
        return patient.model_copy(deep=True) if patient is not None else None

    async def _mock_patient_case_bundle(self, patient_id: str) -> AdminPatientCaseBundleOut | None:
        row = get_mock_case(patient_id)
        if row is None:
            return None
        patient = self._mock_patient_or_none(patient_id)
        context = self._mock_patient_context_or_none(patient_id)
        if patient is None or context is None:
            return None
        department_id = str(row.get("department_id") or MOCK_DEPARTMENT_ID)
        bed = next((item for item in get_dynamic_beds(department_id) if item.current_patient_id == patient_id), None)
        department = await self._department_lookup_admin(department_id)
        return AdminPatientCaseBundleOut(patient=patient, context=context, bed=bed, department=department)

    @staticmethod
    def _build_empty_bed_context(bed_no: str) -> PatientContextOut:
        value = str(bed_no or "").strip() or "未知"
        return PatientContextOut(
            patient_id=f"bed-vacant-{value}",
            patient_name=None,
            bed_no=value,
            encounter_id=None,
            diagnoses=[],
            risk_tags=[],
            pending_tasks=["当前床位暂无在床患者，请核对床号或直接查询病区重点患者。"],
            latest_observations=[],
            risk_level="低危",
            risk_score=0.0,
            risk_reason="当前床位为空。",
            updated_at=datetime.now(timezone.utc),
        )

    @staticmethod
    def _apply_risk_to_bed(bed: BedOverview) -> BedOverview:
        snapshot = evaluate_clinical_risk(
            risk_tags=bed.risk_tags,
            pending_tasks=bed.pending_tasks,
            latest_observations=[],
            status=bed.status,
        )
        bed.risk_level = snapshot["risk_level"]
        bed.risk_score = snapshot["risk_score"]
        bed.risk_reason = snapshot["risk_reason"]
        return bed

    @staticmethod
    def _apply_risk_to_context(context: PatientContextOut) -> PatientContextOut:
        snapshot = evaluate_clinical_risk(
            risk_tags=context.risk_tags,
            pending_tasks=context.pending_tasks,
            latest_observations=context.latest_observations,
            status="occupied",
        )
        context.risk_level = snapshot["risk_level"]
        context.risk_score = snapshot["risk_score"]
        context.risk_reason = snapshot["risk_reason"]
        return context

    def _engine_or_none(self) -> AsyncEngine | None:
        if not self._db_enabled():
            return None
        if self._engine is None:
            self._engine = create_async_engine(settings.postgres_dsn, pool_pre_ping=True)
        return self._engine

    @staticmethod
    def _mock_patient_context_or_none(patient_id: str) -> PatientContextOut | None:
        context = get_dynamic_context(patient_id)
        if context is None:
            return None
        return context.model_copy(deep=True)

    @staticmethod
    def _doc_status_label(status: str) -> str:
        mapping = {
            "draft": "草稿",
            "reviewed": "已审核",
            "submitted": "已提交",
            "saved": "已保存",
        }
        return mapping.get(status, status or "未知")

    @staticmethod
    def _format_doc_sync(status: str, updated_at: str | None) -> str:
        label = PatientContextRepository._doc_status_label(status)
        if not updated_at:
            return f"文书状态：{label}"
        short_time = updated_at.replace("T", " ").replace("Z", "")
        if len(short_time) >= 19:
            short_time = short_time[5:19]
        return f"文书状态：{label}（{short_time}）"
    async def _latest_document_hint(self, patient_id: str | None, requested_by: str | None = None) -> dict[str, Any] | None:
        if not patient_id:
            return None
        try:
            params: dict[str, Any] = {}
            owner = (requested_by or "").strip()
            if owner:
                params["requested_by"] = owner
            async with httpx.AsyncClient(timeout=4, trust_env=False) as client:
                response = await client.get(f"{settings.document_service_url}/document/drafts/{patient_id}", params=params or None)
            if response.status_code >= 400:
                return None
            drafts = response.json()
            if not isinstance(drafts, list) or not drafts:
                return None
            latest = drafts[0]
            status = str(latest.get("status", "draft"))
            updated_at = latest.get("updated_at")
            document_type = latest.get("document_type")
            draft_text = str(latest.get("draft_text", "")).replace("\n", " ").strip()
            excerpt = draft_text[:70] + ("..." if len(draft_text) > 70 else "")
            sync_text = self._format_doc_sync(status, updated_at)
            return {
                "sync_text": sync_text,
                "status": status,
                "updated_at": updated_at,
                "document_type": document_type,
                "excerpt": excerpt,
            }
        except Exception:
            return None

    def _merge_document_hint_to_context(
        self,
        context: PatientContextOut,
        document_hint: dict[str, Any] | None,
    ) -> PatientContextOut:
        if not document_hint:
            return context
        sync_text = document_hint.get("sync_text")
        context.latest_document_sync = sync_text
        context.latest_document_status = document_hint.get("status")
        context.latest_document_type = document_hint.get("document_type")
        context.latest_document_excerpt = document_hint.get("excerpt")
        raw_updated_at = document_hint.get("updated_at")
        if isinstance(raw_updated_at, str):
            try:
                context.latest_document_updated_at = datetime.fromisoformat(raw_updated_at.replace("Z", "+00:00"))
            except Exception:
                context.latest_document_updated_at = None
        return context

    @staticmethod
    def _normalize_admin_case_match(item: AdminPatientCaseOut, query: str) -> bool:
        q = (query or "").strip().lower()
        if not q:
            return True
        haystack = " ".join(
            [
                str(item.bed_no or ""),
                str(item.room_no or ""),
                str(item.full_name or ""),
                str(item.mrn or ""),
                str(item.inpatient_no or ""),
                " ".join(item.diagnoses),
                " ".join(item.pending_tasks),
            ]
        ).lower()
        return q in haystack

    @staticmethod
    def _build_hotspot(case: AdminPatientCaseOut) -> WardHotspotOut:
        latest_observation = ""
        if case.latest_observations:
            latest = case.latest_observations[0]
            latest_observation = " ".join([str(latest.get("name") or "").strip(), str(latest.get("value") or "").strip()]).strip()
        reasons = [item for item in [case.risk_reason, *(case.risk_tags or [])] if item]
        return WardHotspotOut(
            patient_id=case.patient_id,
            bed_no=case.bed_no,
            patient_name=case.full_name,
            score=float(case.risk_score or 0),
            reasons=reasons[:4],
            latest_observation=latest_observation or None,
        )

    async def _department_lookup_admin(self, department_id: str) -> DepartmentAdminOut | None:
        wanted = str(department_id or "").strip()
        if not wanted:
            return None
        rows = await self.list_departments_admin()
        for item in rows:
            if item.id == wanted or str(item.code or "") == wanted:
                return item
        return None

    async def _build_admin_case_out(
        self,
        *,
        patient: PatientBase,
        context: PatientContextOut,
        bed: BedOverview | None,
        department_name: str | None = None,
    ) -> AdminPatientCaseOut:
        department_id = bed.department_id if bed else (await self._department_lookup_admin(MOCK_DEPARTMENT_ID) or DepartmentAdminOut(id=MOCK_DEPARTMENT_ID, name=MOCK_DEPARTMENT_ID)).id
        department = await self._department_lookup_admin(department_id)
        return AdminPatientCaseOut(
            patient_id=patient.id,
            encounter_id=context.encounter_id,
            department_id=department_id,
            department_name=department_name or department.name if department else department_id,
            bed_no=bed.bed_no if bed else context.bed_no,
            room_no=bed.room_no if bed else None,
            mrn=patient.mrn,
            inpatient_no=patient.inpatient_no,
            full_name=patient.full_name,
            gender=patient.gender,
            age=patient.age,
            blood_type=patient.blood_type,
            allergy_info=patient.allergy_info,
            current_status=patient.current_status,
            diagnoses=list(context.diagnoses),
            risk_tags=list(context.risk_tags),
            pending_tasks=list(context.pending_tasks),
            latest_observations=[dict(item) for item in context.latest_observations],
            risk_level=context.risk_level,
            risk_score=context.risk_score,
            risk_reason=context.risk_reason,
            latest_document_sync=context.latest_document_sync,
            updated_at=context.updated_at,
        )

    async def list_departments_admin(self) -> list[DepartmentAdminOut]:
        if not self._db_enabled():
            return list_mock_departments()

        engine = self._engine_or_none()
        if engine is None:
            return list_mock_departments() if self._should_use_demo_fallback(MOCK_DEPARTMENT_ID) else []

        query = text(
            """
            SELECT
                d.id::text AS id,
                d.code AS code,
                COALESCE(d.name, d.code, d.id::text) AS name,
                COUNT(b.id) AS bed_count,
                COUNT(b.current_patient_id) AS occupied_count
            FROM departments d
            LEFT JOIN beds b ON b.department_id = d.id
            GROUP BY d.id, d.code, d.name
            ORDER BY COALESCE(d.name, d.code, d.id::text)
            """
        )
        try:
            async with engine.connect() as conn:
                rows = (await conn.execute(query)).mappings().all()
        except Exception as exc:
            self._mark_db_unavailable(f"list_departments_admin:{exc}")
            return list_mock_departments() if self._should_use_demo_fallback(MOCK_DEPARTMENT_ID) else []

        if not rows and self._should_use_demo_fallback(MOCK_DEPARTMENT_ID):
            return list_mock_departments()
        return [
            DepartmentAdminOut(
                id=str(row["id"]),
                code=row.get("code"),
                name=str(row.get("name") or row["id"]),
                location="统一病区源",
                bed_count=int(row.get("bed_count") or 0),
                occupied_count=int(row.get("occupied_count") or 0),
            )
            for row in rows
        ]

    async def get_ward_analytics_admin(self, department_id: str) -> WardAnalyticsOut:
        department = await self._department_lookup_admin(department_id)
        beds = await self.get_ward_beds(department.id if department else department_id)
        cases = await self.list_patient_cases_admin(department.id if department else department_id, limit=300)
        occupied = [item for item in beds if item.current_patient_id]
        hotspots = sorted(cases, key=lambda item: float(item.risk_score or 0), reverse=True)[:8]
        return WardAnalyticsOut(
            department_id=department.id if department else department_id,
            department_name=department.name if department else department_id,
            total_beds=len(beds),
            occupied_beds=len(occupied),
            vacant_beds=max(0, len(beds) - len(occupied)),
            admitted_cases=len([item for item in cases if item.current_status == "admitted"]),
            hotspots=[self._build_hotspot(item) for item in hotspots],
        )

    async def list_patient_cases_admin(
        self,
        department_id: str,
        *,
        query: str = "",
        current_status: str | None = None,
        limit: int = 200,
    ) -> list[AdminPatientCaseOut]:
        requested_department = await self._department_lookup_admin(department_id)
        resolved_department_id = requested_department.id if requested_department else department_id
        status_filter = (current_status or "").strip().lower()

        if not self._db_enabled():
            rows = list_mock_cases(resolved_department_id, query=query, current_status=current_status, limit=limit)
            return [
                AdminPatientCaseOut(**item)
                for item in rows
            ]

        beds = await self.get_ward_beds(resolved_department_id)

        async def build_case(bed: BedOverview) -> AdminPatientCaseOut | None:
            if not bed.current_patient_id:
                return None
            patient, context = await asyncio.gather(
                self.get_patient(bed.current_patient_id),
                self.get_patient_context(bed.current_patient_id, include_document_hint=False),
            )
            if patient is None or context is None:
                return None
            item = await self._build_admin_case_out(
                patient=patient,
                context=context,
                bed=bed,
                department_name=requested_department.name if requested_department else None,
            )
            if status_filter and str(item.current_status or "").lower() != status_filter:
                return None
            if not self._normalize_admin_case_match(item, query):
                return None
            return item

        cases = [item for item in await asyncio.gather(*[build_case(bed) for bed in beds if bed.current_patient_id]) if item]
        cases.sort(key=lambda item: self._bed_sort_key(item.bed_no))
        if not cases and self._should_use_demo_fallback(resolved_department_id):
            rows = list_mock_cases(resolved_department_id, query=query, current_status=current_status, limit=limit)
            return [AdminPatientCaseOut(**item) for item in rows]
        return cases[: max(1, int(limit or 200))]

    async def get_patient_case_bundle_admin(self, patient_id: str) -> AdminPatientCaseBundleOut | None:
        mock_bundle = await self._mock_patient_case_bundle(patient_id)
        if mock_bundle is not None:
            return mock_bundle
        if not self._db_enabled():
            row = get_mock_case(patient_id)
            if row is None:
                return None
            patient = MOCK_PATIENTS.get(patient_id)
            context = self._mock_patient_context_or_none(patient_id)
            if patient is None or context is None:
                return None
            bed = next((item.model_copy(deep=True) for item in MOCK_BEDS if item.current_patient_id == patient_id), None)
            department = await self._department_lookup_admin(row["department_id"])
            return AdminPatientCaseBundleOut(patient=patient, context=context, bed=bed, department=department)

        patient = await self.get_patient(patient_id)
        context = await self.get_patient_context(patient_id)
        if patient is None or context is None:
            return None
        bed = next((item for item in await self.get_all_beds() if item.current_patient_id == patient_id), None)
        department = await self._department_lookup_admin(bed.department_id if bed else MOCK_DEPARTMENT_ID)
        return AdminPatientCaseBundleOut(patient=patient, context=context, bed=bed, department=department)

    async def upsert_patient_case_admin(self, payload: AdminPatientCaseUpsertRequest) -> AdminPatientCaseBundleOut:
        normalized_department = await self._department_lookup_admin(payload.department_id)
        resolved_department_id = normalized_department.id if normalized_department else payload.department_id
        observations = [dict(item) for item in payload.latest_observations]

        if not self._db_enabled():
            row = upsert_mock_case(
                patient_id=payload.patient_id,
                encounter_id=payload.encounter_id,
                department_id=resolved_department_id,
                bed_no=payload.bed_no,
                room_no=payload.room_no,
                mrn=payload.mrn,
                inpatient_no=payload.inpatient_no,
                full_name=payload.full_name,
                gender=payload.gender,
                age=payload.age,
                blood_type=payload.blood_type,
                allergy_info=payload.allergy_info,
                current_status=payload.current_status,
                diagnoses=payload.diagnoses,
                risk_tags=payload.risk_tags,
                pending_tasks=payload.pending_tasks,
                latest_observations=observations,
            )
            bundle = await self.get_patient_case_bundle_admin(row["patient_id"])
            if bundle is None:
                raise ValueError("case_save_failed")
            return bundle

        engine = self._engine_or_none()
        if engine is None:
            if self._should_use_demo_fallback(resolved_department_id):
                row = upsert_mock_case(
                    patient_id=payload.patient_id,
                    encounter_id=payload.encounter_id,
                    department_id=resolved_department_id,
                    bed_no=payload.bed_no,
                    room_no=payload.room_no,
                    mrn=payload.mrn,
                    inpatient_no=payload.inpatient_no,
                    full_name=payload.full_name,
                    gender=payload.gender,
                    age=payload.age,
                    blood_type=payload.blood_type,
                    allergy_info=payload.allergy_info,
                    current_status=payload.current_status,
                    diagnoses=payload.diagnoses,
                    risk_tags=payload.risk_tags,
                    pending_tasks=payload.pending_tasks,
                    latest_observations=observations,
                )
                bundle = await self.get_patient_case_bundle_admin(row["patient_id"])
                if bundle is None:
                    raise ValueError("case_save_failed")
                return bundle
            raise ValueError("db_unavailable")

        patient_id = str(payload.patient_id or "").strip() or str(uuid4())
        encounter_id = str(payload.encounter_id or "").strip() or str(uuid4())
        current_status = str(payload.current_status or "admitted").strip() or "admitted"

        if not self._is_uuid_like(patient_id) or not self._is_uuid_like(encounter_id):
            row = upsert_mock_case(
                patient_id=patient_id,
                encounter_id=encounter_id,
                department_id=resolved_department_id,
                bed_no=payload.bed_no,
                room_no=payload.room_no,
                mrn=payload.mrn,
                inpatient_no=payload.inpatient_no,
                full_name=payload.full_name,
                gender=payload.gender,
                age=payload.age,
                blood_type=payload.blood_type,
                allergy_info=payload.allergy_info,
                current_status=payload.current_status,
                diagnoses=payload.diagnoses,
                risk_tags=payload.risk_tags,
                pending_tasks=payload.pending_tasks,
                latest_observations=observations,
            )
            bundle = await self.get_patient_case_bundle_admin(row["patient_id"])
            if bundle is None:
                raise ValueError("case_save_failed")
            return bundle

        patient_sql = text(
            """
            INSERT INTO patients (id, mrn, inpatient_no, full_name, gender, age, blood_type, allergy_info, current_status)
            VALUES (:id, :mrn, :inpatient_no, :full_name, :gender, :age, :blood_type, :allergy_info, :current_status)
            ON CONFLICT (id)
            DO UPDATE SET
                mrn = EXCLUDED.mrn,
                inpatient_no = EXCLUDED.inpatient_no,
                full_name = EXCLUDED.full_name,
                gender = EXCLUDED.gender,
                age = EXCLUDED.age,
                blood_type = EXCLUDED.blood_type,
                allergy_info = EXCLUDED.allergy_info,
                current_status = EXCLUDED.current_status
            """
        )
        close_encounters_sql = text(
            """
            UPDATE encounters
            SET status = 'closed'
            WHERE patient_id::text = :patient_id
              AND id::text <> :encounter_id
              AND status = 'active'
            """
        )
        upsert_encounter_sql = text(
            """
            INSERT INTO encounters (id, patient_id, status, admission_at)
            VALUES (:id, :patient_id, :status, NOW())
            ON CONFLICT (id)
            DO UPDATE SET
                patient_id = EXCLUDED.patient_id,
                status = EXCLUDED.status
            """
        )
        clear_diagnoses_sql = text(
            """
            DELETE FROM patient_diagnoses
            WHERE encounter_id::text = :encounter_id
            """
        )
        insert_diagnosis_sql = text(
            """
            INSERT INTO patient_diagnoses (encounter_id, diagnosis_name, status, created_at)
            VALUES (:encounter_id, :diagnosis_name, 'active', NOW())
            """
        )
        clear_observations_sql = text(
            """
            DELETE FROM observations
            WHERE patient_id::text = :patient_id
            """
        )
        insert_observation_sql = text(
            """
            INSERT INTO observations (patient_id, name, value_text, abnormal_flag, observed_at)
            VALUES (:patient_id, :name, :value_text, :abnormal_flag, NOW())
            """
        )
        clear_tasks_sql = text(
            """
            DELETE FROM care_tasks
            WHERE patient_id::text = :patient_id
              AND status IN ('pending', 'in_progress')
            """
        )
        insert_task_sql = text(
            """
            INSERT INTO care_tasks (patient_id, title, status, priority, created_at)
            VALUES (:patient_id, :title, 'pending', :priority, NOW())
            """
        )
        find_bed_sql = text(
            """
            SELECT id::text AS id, current_patient_id::text AS current_patient_id
            FROM beds
            WHERE bed_no = :bed_no
              AND department_id::text = :department_id
            LIMIT 1
            """
        )
        clear_old_beds_sql = text(
            """
            UPDATE beds
            SET current_patient_id = NULL, status = 'vacant'
            WHERE current_patient_id::text = :patient_id
            """
        )
        update_bed_sql = text(
            """
            UPDATE beds
            SET room_no = :room_no,
                current_patient_id = :current_patient_id,
                status = :status
            WHERE id::text = :bed_id
            """
        )
        insert_bed_sql = text(
            """
            INSERT INTO beds (id, department_id, bed_no, room_no, status, current_patient_id)
            VALUES (:id, :department_id, :bed_no, :room_no, :status, :current_patient_id)
            """
        )

        try:
            async with engine.begin() as conn:
                await conn.execute(
                    patient_sql,
                    {
                        "id": patient_id,
                        "mrn": payload.mrn or "",
                        "inpatient_no": payload.inpatient_no,
                        "full_name": payload.full_name,
                        "gender": payload.gender,
                        "age": payload.age,
                        "blood_type": payload.blood_type,
                        "allergy_info": payload.allergy_info,
                        "current_status": current_status,
                    },
                )
                await conn.execute(close_encounters_sql, {"patient_id": patient_id, "encounter_id": encounter_id})
                await conn.execute(
                    upsert_encounter_sql,
                    {
                        "id": encounter_id,
                        "patient_id": patient_id,
                        "status": "active" if current_status == "admitted" else "closed",
                    },
                )
                await conn.execute(clear_diagnoses_sql, {"encounter_id": encounter_id})
                for diagnosis_name in [str(item).strip() for item in payload.diagnoses if str(item).strip()]:
                    await conn.execute(insert_diagnosis_sql, {"encounter_id": encounter_id, "diagnosis_name": diagnosis_name})

                await conn.execute(clear_observations_sql, {"patient_id": patient_id})
                for item in observations:
                    name = str(item.get("name") or "").strip()
                    value_text = str(item.get("value") or "").strip()
                    if not name and not value_text:
                        continue
                    await conn.execute(
                        insert_observation_sql,
                        {
                            "patient_id": patient_id,
                            "name": name,
                            "value_text": value_text,
                            "abnormal_flag": str(item.get("abnormal_flag") or "normal"),
                        },
                    )

                await conn.execute(clear_tasks_sql, {"patient_id": patient_id})
                for priority, title in enumerate([str(item).strip() for item in payload.pending_tasks if str(item).strip()], start=1):
                    await conn.execute(insert_task_sql, {"patient_id": patient_id, "title": title, "priority": priority})

                target_bed = (await conn.execute(find_bed_sql, {"bed_no": payload.bed_no, "department_id": resolved_department_id})).mappings().first()
                if target_bed and target_bed.get("current_patient_id") and target_bed.get("current_patient_id") != patient_id:
                    raise ValueError("bed_occupied")
                await conn.execute(clear_old_beds_sql, {"patient_id": patient_id})
                bed_status = "occupied" if current_status == "admitted" else "vacant"
                current_patient_id = patient_id if current_status == "admitted" else None
                if target_bed:
                    await conn.execute(
                        update_bed_sql,
                        {
                            "bed_id": target_bed["id"],
                            "room_no": payload.room_no,
                            "current_patient_id": current_patient_id,
                            "status": bed_status,
                        },
                    )
                else:
                    await conn.execute(
                        insert_bed_sql,
                        {
                            "id": str(uuid4()),
                            "department_id": resolved_department_id,
                            "bed_no": payload.bed_no,
                            "room_no": payload.room_no,
                            "status": bed_status,
                            "current_patient_id": current_patient_id,
                        },
                    )
        except ValueError:
            raise
        except Exception as exc:
            self._mark_db_unavailable(f"upsert_patient_case_admin:{exc}", cooldown_sec=20)
            if self._should_use_demo_fallback(resolved_department_id):
                row = upsert_mock_case(
                    patient_id=payload.patient_id,
                    encounter_id=payload.encounter_id,
                    department_id=resolved_department_id,
                    bed_no=payload.bed_no,
                    room_no=payload.room_no,
                    mrn=payload.mrn,
                    inpatient_no=payload.inpatient_no,
                    full_name=payload.full_name,
                    gender=payload.gender,
                    age=payload.age,
                    blood_type=payload.blood_type,
                    allergy_info=payload.allergy_info,
                    current_status=payload.current_status,
                    diagnoses=payload.diagnoses,
                    risk_tags=payload.risk_tags,
                    pending_tasks=payload.pending_tasks,
                    latest_observations=observations,
                )
                bundle = await self.get_patient_case_bundle_admin(row["patient_id"])
                if bundle is not None:
                    return bundle
            raise

        bundle = await self.get_patient_case_bundle_admin(patient_id)
        if bundle is None:
            raise ValueError("case_save_failed")
        return bundle

    async def get_ward_beds(self, department_id: str) -> list[BedOverview]:
        if not self._db_enabled():
            if self._should_use_demo_fallback(department_id):
                beds = self._mock_beds_by_department(department_id)
                return self._augment_with_virtual_beds(beds, department_id=department_id)
            return []

        engine = self._engine_or_none()
        if engine is None:
            if self._should_use_demo_fallback(department_id):
                beds = self._mock_beds_by_department(department_id)
                return self._augment_with_virtual_beds(beds, department_id=department_id)
            return []

        query = text(
            """
            SELECT
                b.id::text AS id,
                b.department_id::text AS department_id,
                b.bed_no,
                b.room_no,
                b.status,
                b.current_patient_id::text AS current_patient_id,
                p.full_name AS patient_name
            FROM beds b
            JOIN departments d ON d.id = b.department_id
            LEFT JOIN patients p ON p.id = b.current_patient_id
            WHERE b.department_id::text = :department_id
               OR d.code = :department_id
            ORDER BY b.bed_no
            """
        )

        try:
            async with engine.connect() as conn:
                rows = (await conn.execute(query, {"department_id": department_id})).mappings().all()
        except Exception as exc:
            self._mark_db_unavailable(f"get_ward_beds:{exc}")
            if self._should_use_demo_fallback(department_id):
                beds = self._mock_beds_by_department(department_id)
                return self._augment_with_virtual_beds(beds, department_id=department_id)
            return []

        beds: list[BedOverview] = []
        for row in rows:
            current_patient_id = row["current_patient_id"]
            pending_tasks = await self._pending_tasks_for_patient(current_patient_id)
            risk_tags = await self._risk_tags_for_patient(current_patient_id)
            beds.append(
                self._apply_risk_to_bed(
                    BedOverview(
                    id=row["id"],
                    department_id=row["department_id"],
                    bed_no=row["bed_no"],
                    room_no=row["room_no"],
                    status=row["status"],
                    current_patient_id=current_patient_id,
                    patient_name=row["patient_name"],
                    pending_tasks=pending_tasks,
                    risk_tags=risk_tags,
                    )
                )
            )
        overlay_beds = get_dynamic_beds(department_id)
        if overlay_beds:
            beds = self._merge_bed_snapshots(beds, overlay_beds)
        elif not beds and self._should_use_demo_fallback(department_id):
            beds = self._mock_beds_by_department(department_id)
        return self._augment_with_virtual_beds(beds, department_id=department_id)

    async def get_all_beds(self) -> list[BedOverview]:
        if not self._db_enabled():
            if self._should_use_demo_fallback(MOCK_DEPARTMENT_ID):
                beds = self._mock_all_beds()
                return self._augment_with_virtual_beds(beds, department_id=MOCK_DEPARTMENT_ID)
            return []

        engine = self._engine_or_none()
        if engine is None:
            if self._should_use_demo_fallback(MOCK_DEPARTMENT_ID):
                beds = self._mock_all_beds()
                return self._augment_with_virtual_beds(beds, department_id=MOCK_DEPARTMENT_ID)
            return []

        query = text(
            """
            SELECT
                b.id::text AS id,
                b.department_id::text AS department_id,
                b.bed_no,
                b.room_no,
                b.status,
                b.current_patient_id::text AS current_patient_id,
                p.full_name AS patient_name
            FROM beds b
            LEFT JOIN patients p ON p.id = b.current_patient_id
            ORDER BY b.bed_no
            """
        )

        try:
            async with engine.connect() as conn:
                rows = (await conn.execute(query)).mappings().all()
        except Exception as exc:
            self._mark_db_unavailable(f"get_all_beds:{exc}")
            if self._should_use_demo_fallback(MOCK_DEPARTMENT_ID):
                beds = self._mock_all_beds()
                return self._augment_with_virtual_beds(beds, department_id=MOCK_DEPARTMENT_ID)
            return []

        beds: list[BedOverview] = []
        for row in rows:
            current_patient_id = row["current_patient_id"]
            pending_tasks = await self._pending_tasks_for_patient(current_patient_id)
            risk_tags = await self._risk_tags_for_patient(current_patient_id)
            beds.append(
                self._apply_risk_to_bed(
                    BedOverview(
                    id=row["id"],
                    department_id=row["department_id"],
                    bed_no=row["bed_no"],
                    room_no=row["room_no"],
                    status=row["status"],
                    current_patient_id=current_patient_id,
                    patient_name=row["patient_name"],
                    pending_tasks=pending_tasks,
                    risk_tags=risk_tags,
                    )
                )
            )
        overlay_beds: list[BedOverview] = []
        for department_id in sorted({str(item.department_id or "").strip() for item in MOCK_BEDS if str(item.department_id or "").strip()}):
            overlay_beds.extend(get_dynamic_beds(department_id))
        if overlay_beds:
            beds = self._merge_bed_snapshots(beds, overlay_beds)
        elif not beds and self._should_use_demo_fallback(MOCK_DEPARTMENT_ID):
            beds = self._mock_all_beds()
        return self._augment_with_virtual_beds(beds, department_id=MOCK_DEPARTMENT_ID)

    async def get_patient(self, patient_id: str) -> PatientBase | None:
        mock_patient = self._mock_patient_or_none(patient_id)
        if mock_patient is not None:
            return mock_patient
        if not self._db_enabled():
            if self._should_use_demo_fallback(MOCK_DEPARTMENT_ID):
                return MOCK_PATIENTS.get(patient_id)
            return None

        engine = self._engine_or_none()
        if engine is None:
            return None

        query = text(
            """
            SELECT
                id::text AS id,
                mrn,
                inpatient_no,
                full_name,
                gender,
                age,
                blood_type,
                allergy_info,
                current_status
            FROM patients
            WHERE id::text = :patient_id
            """
        )
        try:
            async with engine.connect() as conn:
                row = (await conn.execute(query, {"patient_id": patient_id})).mappings().first()
        except Exception as exc:
            self._mark_db_unavailable(f"get_patient:{exc}")
            if self._should_use_demo_fallback(MOCK_DEPARTMENT_ID):
                return MOCK_PATIENTS.get(patient_id)
            return None
        if row:
            return PatientBase(**row)
        if self._should_use_demo_fallback(MOCK_DEPARTMENT_ID):
            return MOCK_PATIENTS.get(patient_id)
        return None

    async def get_patient_context(
        self,
        patient_id: str,
        requested_by: str | None = None,
        *,
        include_document_hint: bool = True,
    ) -> PatientContextOut | None:
        mock_context = self._mock_patient_context_or_none(patient_id)
        if mock_context is not None:
            if not include_document_hint:
                return mock_context
            document_hint = await self._latest_document_hint(patient_id, requested_by=requested_by)
            return self._merge_document_hint_to_context(mock_context, document_hint)
        if not self._db_enabled():
            if not self._should_use_demo_fallback(MOCK_DEPARTMENT_ID):
                return None
            ctx = self._mock_patient_context_or_none(patient_id)
            if ctx is None:
                return None
            document_hint = await self._latest_document_hint(patient_id, requested_by=requested_by) if include_document_hint else None
            return self._merge_document_hint_to_context(ctx, document_hint)

        engine = self._engine_or_none()
        if engine is None:
            return None

        encounter_query = text(
            """
            SELECT id::text AS encounter_id
            FROM encounters
            WHERE patient_id::text = :patient_id AND status = 'active'
            ORDER BY admission_at DESC NULLS LAST
            LIMIT 1
            """
        )
        patient_query = text(
            """
            SELECT full_name
            FROM patients
            WHERE id::text = :patient_id
            LIMIT 1
            """
        )
        bed_query = text(
            """
            SELECT bed_no
            FROM beds
            WHERE current_patient_id::text = :patient_id
            LIMIT 1
            """
        )
        diagnosis_query = text(
            """
            SELECT d.diagnosis_name
            FROM patient_diagnoses d
            JOIN encounters e ON e.id = d.encounter_id
            WHERE e.patient_id::text = :patient_id AND d.status = 'active'
            ORDER BY d.created_at DESC
            LIMIT 8
            """
        )
        obs_query = text(
            """
            SELECT name, value_num, value_text, unit, abnormal_flag, observed_at
            FROM observations
            WHERE patient_id::text = :patient_id
            ORDER BY observed_at DESC
            LIMIT 8
            """
        )

        try:
            async with engine.connect() as conn:
                encounter_row = (await conn.execute(encounter_query, {"patient_id": patient_id})).mappings().first()
                patient_row = (await conn.execute(patient_query, {"patient_id": patient_id})).mappings().first()
                bed_row = (await conn.execute(bed_query, {"patient_id": patient_id})).mappings().first()
                diagnosis_rows = (await conn.execute(diagnosis_query, {"patient_id": patient_id})).mappings().all()
                obs_rows = (await conn.execute(obs_query, {"patient_id": patient_id})).mappings().all()
        except Exception as exc:
            self._mark_db_unavailable(f"get_patient_context:{exc}")
            if not self._should_use_demo_fallback(MOCK_DEPARTMENT_ID):
                return None
            fallback_ctx = self._mock_patient_context_or_none(patient_id)
            if fallback_ctx is None:
                return None
            document_hint = await self._latest_document_hint(patient_id, requested_by=requested_by) if include_document_hint else None
            return self._merge_document_hint_to_context(fallback_ctx, document_hint)

        if not patient_row and self._should_use_demo_fallback(MOCK_DEPARTMENT_ID):
            fallback_ctx = self._mock_patient_context_or_none(patient_id)
            if fallback_ctx is not None:
                document_hint = await self._latest_document_hint(patient_id, requested_by=requested_by) if include_document_hint else None
                return self._merge_document_hint_to_context(fallback_ctx, document_hint)

        pending_tasks = await self._pending_tasks_for_patient(patient_id)
        risk_tags = await self._risk_tags_for_patient(patient_id)

        observations: list[dict[str, Any]] = []
        for row in obs_rows:
            if row["value_text"]:
                value = row["value_text"]
            elif row["value_num"] is not None:
                value = f"{row['value_num']} {row['unit'] or ''}".strip()
            else:
                value = None
            observations.append(
                {
                    "name": row["name"],
                    "value": value,
                    "abnormal_flag": row["abnormal_flag"],
                    "observed_at": row["observed_at"],
                }
            )

        context = PatientContextOut(
            patient_id=patient_id,
            patient_name=patient_row["full_name"] if patient_row else None,
            bed_no=bed_row["bed_no"] if bed_row else None,
            encounter_id=encounter_row["encounter_id"] if encounter_row else None,
            diagnoses=[item["diagnosis_name"] for item in diagnosis_rows],
            risk_tags=risk_tags,
            pending_tasks=pending_tasks,
            latest_observations=observations,
            updated_at=datetime.now(timezone.utc),
        )
        context = self._apply_risk_to_context(context)
        document_hint = await self._latest_document_hint(patient_id, requested_by=requested_by) if include_document_hint else None
        return self._merge_document_hint_to_context(context, document_hint)

    async def find_context_by_bed(
        self,
        bed_no: str,
        department_id: str | None = None,
        requested_by: str | None = None,
    ) -> PatientContextOut | None:
        if not self._db_enabled():
            if not self._should_use_demo_fallback(department_id or MOCK_DEPARTMENT_ID):
                return None
            mock_source = self._mock_beds_by_department(department_id or MOCK_DEPARTMENT_ID) if department_id else [item.model_copy(deep=True) for item in MOCK_BEDS]
            for bed in mock_source:
                if bed.bed_no != bed_no:
                    continue
                if bed.current_patient_id:
                    return await self.get_patient_context(
                        bed.current_patient_id,
                        requested_by=requested_by,
                        include_document_hint=False,
                    )
            return None

        engine = self._engine_or_none()
        if engine is None:
            return None

        if department_id:
            query = text(
                """
                SELECT bed_no, status, current_patient_id::text AS patient_id
                FROM beds
                JOIN departments d ON d.id = beds.department_id
                WHERE bed_no = :bed_no
                  AND (
                      department_id::text = :department_id
                      OR d.code = :department_id
                  )
                LIMIT 1
                """
            )
            params = {"bed_no": bed_no, "department_id": department_id}
        else:
            query = text(
                """
                SELECT bed_no, status, current_patient_id::text AS patient_id
                FROM beds
                WHERE bed_no = :bed_no
                LIMIT 1
                """
            )
            params = {"bed_no": bed_no}

        try:
            async with engine.connect() as conn:
                row = (await conn.execute(query, params)).mappings().first()
        except Exception as exc:
            self._mark_db_unavailable(f"find_context_by_bed:{exc}")
            if not self._should_use_demo_fallback(department_id or MOCK_DEPARTMENT_ID):
                return None
            mock_source = self._mock_beds_by_department(department_id or MOCK_DEPARTMENT_ID) if department_id else [item.model_copy(deep=True) for item in MOCK_BEDS]
            for bed in mock_source:
                if bed.bed_no == bed_no and bed.current_patient_id:
                    return await self.get_patient_context(
                        bed.current_patient_id,
                        requested_by=requested_by,
                        include_document_hint=False,
                    )
            return None
        if not row:
            if self._should_use_demo_fallback(department_id or MOCK_DEPARTMENT_ID):
                mock_source = self._mock_beds_by_department(department_id or MOCK_DEPARTMENT_ID) if department_id else [item.model_copy(deep=True) for item in MOCK_BEDS]
                for bed in mock_source:
                    if bed.bed_no == bed_no and bed.current_patient_id:
                        return await self.get_patient_context(
                            bed.current_patient_id,
                            requested_by=requested_by,
                            include_document_hint=False,
                        )
            if self._virtual_range_contains(bed_no):
                return self._build_empty_bed_context(bed_no)
            return None
        patient_id = str(row.get("patient_id") or "").strip()
        if not patient_id:
            return self._build_empty_bed_context(str(row.get("bed_no") or bed_no))
        return await self.get_patient_context(row["patient_id"], requested_by=requested_by, include_document_hint=False)

    async def get_patient_orders(self, patient_id: str) -> OrderListOut:
        # 当前迭代先走 mock 闭环，确保手机端可完整演示医嘱核对-执行-留痕流程。
        orders = get_active_orders_for_patient(patient_id)
        stats = get_order_stats(patient_id)
        return OrderListOut(
            patient_id=patient_id,
            stats=stats,
            orders=orders,
        )

    async def get_patient_order_history(self, patient_id: str, limit: int = 50) -> list[OrderOut]:
        history = get_order_history_for_patient(patient_id)
        return history[:limit]

    async def double_check_order(self, order_id: str, checked_by: str, note: str | None = None) -> OrderOut | None:
        return mark_order_checked(order_id=order_id, checked_by=checked_by, note=note)

    async def execute_order(self, order_id: str, executed_by: str, note: str | None = None) -> OrderOut | None:
        return mark_order_executed(order_id=order_id, executed_by=executed_by, note=note)

    async def report_order_exception(self, order_id: str, reported_by: str, reason: str) -> OrderOut | None:
        return mark_order_exception(order_id=order_id, reported_by=reported_by, reason=reason)

    async def create_order_request(
        self,
        *,
        patient_id: str,
        requested_by: str,
        title: str,
        details: str,
        priority: str = "P2",
    ) -> OrderOut:
        return create_order_request(
            patient_id=patient_id,
            requested_by=requested_by,
            title=title,
            details=details,
            priority=priority,
        )

    async def _pending_tasks_for_patient(self, patient_id: str | None) -> list[str]:
        if not patient_id:
            return []
        if not self._db_enabled():
            if not self._mock_fallback_enabled():
                return []
            context = self._mock_patient_context_or_none(patient_id)
            base_tasks = context.pending_tasks if context else []
            stats = get_order_stats(patient_id)
            order_hint: list[str] = []
            if stats.get("pending", 0) > 0:
                order_hint.append(f"医嘱待执行 {stats['pending']} 项")
            if stats.get("due_30m", 0) > 0:
                order_hint.append(f"30分钟内到时医嘱 {stats['due_30m']} 项")
            if stats.get("overdue", 0) > 0:
                order_hint.append(f"超时医嘱 {stats['overdue']} 项")
            return [*order_hint, *base_tasks]

        engine = self._engine_or_none()
        if engine is None:
            return []

        query = text(
            """
            SELECT title
            FROM care_tasks
            WHERE patient_id::text = :patient_id
              AND status IN ('pending', 'in_progress')
            ORDER BY priority ASC, created_at DESC
            LIMIT 8
            """
        )
        try:
            async with engine.connect() as conn:
                rows = (await conn.execute(query, {"patient_id": patient_id})).mappings().all()
        except Exception as exc:
            self._mark_db_unavailable(f"_pending_tasks_for_patient:{exc}")
            if not self._mock_fallback_enabled():
                return []
            context = self._mock_patient_context_or_none(patient_id)
            base_tasks = context.pending_tasks if context else []
            stats = get_order_stats(patient_id)
            order_hint: list[str] = []
            if stats.get("pending", 0) > 0:
                order_hint.append(f"医嘱待执行 {stats['pending']} 项")
            if stats.get("due_30m", 0) > 0:
                order_hint.append(f"30分钟内到时医嘱 {stats['due_30m']} 项")
            if stats.get("overdue", 0) > 0:
                order_hint.append(f"超时医嘱 {stats['overdue']} 项")
            return [*order_hint, *base_tasks]
        return [row["title"] for row in rows]

    async def _risk_tags_for_patient(self, patient_id: str | None) -> list[str]:
        if not patient_id:
            return []
        if not self._db_enabled():
            if not self._mock_fallback_enabled():
                return []
            context = self._mock_patient_context_or_none(patient_id)
            return context.risk_tags if context else []

        engine = self._engine_or_none()
        if engine is None:
            return []

        query = text(
            """
            SELECT DISTINCT name, abnormal_flag
            FROM observations
            WHERE patient_id::text = :patient_id
              AND abnormal_flag IN ('high', 'low', 'critical')
              AND observed_at >= NOW() - INTERVAL '24 hours'
            ORDER BY name
            LIMIT 8
            """
        )
        try:
            async with engine.connect() as conn:
                rows = (await conn.execute(query, {"patient_id": patient_id})).mappings().all()
        except Exception as exc:
            self._mark_db_unavailable(f"_risk_tags_for_patient:{exc}")
            if not self._mock_fallback_enabled():
                return []
            context = self._mock_patient_context_or_none(patient_id)
            return context.risk_tags if context else []
        return [f"{row['name']}({row['abnormal_flag']})" for row in rows]


repository = PatientContextRepository()

