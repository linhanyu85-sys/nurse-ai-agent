from __future__ import annotations

from datetime import datetime

from fastapi.testclient import TestClient

from app.main import app
from app.schemas.document import DocumentDraft, DocumentTemplate


def test_create_draft_prefers_explicit_template_id_over_system_default(monkeypatch) -> None:
    system_template = DocumentTemplate(
        id="tpl-system-nursing-note",
        name="系统标准护理记录单",
        source_type="system",
        document_type="nursing_note",
        trigger_keywords=[],
        source_refs=["system"],
        template_text="【系统标准护理记录单】",
        created_by="system",
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    selected_template = DocumentTemplate(
        id="tpl-import-clinical-note",
        name="导入临床护理记录单",
        source_type="import",
        document_type="nursing_note",
        trigger_keywords=["护理记录"],
        source_refs=["custom-import"],
        template_text="【导入临床护理记录单】",
        created_by="u_tester",
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )

    captured: dict[str, str | None] = {}

    async def fake_fetch_patient_context(patient_id: str) -> dict[str, str]:
        return {
            "patient_id": patient_id,
            "patient_name": "张晓明",
            "bed_no": "12",
            "encounter_id": "enc-001",
            "mrn": "MRN-001",
            "diagnoses": ["慢性心衰急性加重"],
            "risk_tags": ["低血压风险"],
            "pending_tasks": ["复测血压"],
        }

    async def fake_build_document_draft(*, document_type: str, spoken_text: str | None, context: dict, template_text: str | None, template_name: str | None):
        captured["document_type"] = document_type
        captured["template_text"] = template_text
        captured["template_name"] = template_name
        return (
            f"{template_name} -> {context['patient_name']}",
            {
                "template_name": template_name,
                "template_applied": True,
                "field_summary": {"total": 1, "filled": 1, "missing": 0},
            },
        )

    async def fake_write_audit_log(**_: object) -> None:
        return None

    async def fake_db_create(**_: object):
        return None

    def fake_store_create(**kwargs: object) -> DocumentDraft:
        now = datetime.now()
        return DocumentDraft(
            id="draft-001",
            patient_id=str(kwargs["patient_id"]),
            encounter_id=str(kwargs["encounter_id"]),
            document_type=str(kwargs["document_type"]),
            draft_text=str(kwargs["draft_text"]),
            structured_fields=dict(kwargs["structured_fields"]),
            created_by=str(kwargs["created_by"]),
            created_at=now,
            updated_at=now,
        )

    monkeypatch.setattr("app.api.routes.fetch_patient_context", fake_fetch_patient_context)
    monkeypatch.setattr("app.api.routes.build_document_draft", fake_build_document_draft)
    monkeypatch.setattr("app.api.routes.write_audit_log", fake_write_audit_log)
    monkeypatch.setattr("app.api.routes.document_db_store.create", fake_db_create)
    monkeypatch.setattr("app.api.routes.document_store.get_preferred_template", lambda _document_type: system_template)
    monkeypatch.setattr(
        "app.api.routes.document_store.get_template",
        lambda template_id: selected_template if template_id == selected_template.id else None,
    )
    monkeypatch.setattr("app.api.routes.document_store.create", fake_store_create)

    client = TestClient(app)
    response = client.post(
        "/document/draft",
        json={
            "patient_id": "pat-001",
            "document_type": "nursing_note",
            "spoken_text": "",
            "template_id": selected_template.id,
            "requested_by": "nurse01",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert captured["template_name"] == selected_template.name
    assert captured["template_text"] == selected_template.template_text
    assert payload["structured_fields"]["template_name"] == selected_template.name
    assert payload["structured_fields"]["template_id"] == selected_template.id
    assert payload["structured_fields"]["template_source_type"] == "import"
    assert payload["structured_fields"]["template_snapshot"] == selected_template.template_text
    assert payload["structured_fields"]["requested_by"] == "u_nurse01"
    assert payload["structured_fields"].get("template_locked") is None
    assert payload["draft_text"].startswith(selected_template.name)


def test_create_draft_falls_back_to_payload_context_when_patient_context_unavailable(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_fetch_patient_context(patient_id: str):
        return None

    async def fake_build_document_draft(*, document_type: str, spoken_text: str | None, context: dict, template_text: str | None, template_name: str | None):
        captured["document_type"] = document_type
        captured["context"] = dict(context)
        return (
            f"{template_name} -> {context.get('bed_no') or '-'} / {context.get('patient_name') or '-'}",
            {
                "template_name": template_name,
                "template_applied": True,
                "field_summary": {"total": 2, "filled": 2, "missing": 0},
            },
        )

    async def fake_write_audit_log(**_: object) -> None:
        return None

    async def fake_db_create(**_: object):
        return None

    def fake_store_create(**kwargs: object) -> DocumentDraft:
        now = datetime.now()
        return DocumentDraft(
            id="draft-fallback-001",
            patient_id=str(kwargs["patient_id"]),
            encounter_id=str(kwargs["encounter_id"]) if kwargs["encounter_id"] else None,
            document_type=str(kwargs["document_type"]),
            draft_text=str(kwargs["draft_text"]),
            structured_fields=dict(kwargs["structured_fields"]),
            created_by=str(kwargs["created_by"]),
            created_at=now,
            updated_at=now,
        )

    monkeypatch.setattr("app.api.routes.fetch_patient_context", fake_fetch_patient_context)
    monkeypatch.setattr("app.api.routes.build_document_draft", fake_build_document_draft)
    monkeypatch.setattr("app.api.routes.write_audit_log", fake_write_audit_log)
    monkeypatch.setattr("app.api.routes.document_db_store.create", fake_db_create)
    monkeypatch.setattr("app.api.routes.document_store.create", fake_store_create)

    client = TestClient(app)
    response = client.post(
        "/document/draft",
        json={
            "patient_id": "pat-fallback-001",
            "document_type": "nursing_note",
            "spoken_text": "患者当前意识清楚，拟按模板补录。",
            "template_text": "【护理记录单】\n床号：{{bed_no}}\n姓名：{{patient_name}}",
            "template_name": "护理记录单",
            "requested_by": "u_nurse_01",
            "bed_no": "18",
            "patient_name": "陈伟",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["structured_fields"]["context_source"] == "payload_fallback"
    assert payload["structured_fields"]["template_name"] == "护理记录单"
    assert payload["structured_fields"]["requested_by"] == "u_nurse_01"
    assert payload["draft_text"].startswith("护理记录单")
    assert isinstance(captured.get("context"), dict)
    context = captured["context"]
    assert context["patient_id"] == "pat-fallback-001"
    assert context["bed_no"] == "18"
    assert context["patient_name"] == "陈伟"
    assert context["requested_by"] == "u_nurse_01"
    assert context["context_source"] == "payload_fallback"
