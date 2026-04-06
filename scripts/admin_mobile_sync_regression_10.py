from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


BASE_URL = os.environ.get("ADMIN_MOBILE_SYNC_BASE", "http://127.0.0.1:8000")
ROUND_COUNT = 10
OUTPUT_PATH = Path(".smoke-regression/admin-mobile-sync-regression-10.json")


def request_json(method: str, path: str, *, params: dict | None = None, body: dict | None = None) -> object:
    url = BASE_URL.rstrip("/") + path
    if params:
        query = urllib.parse.urlencode({key: value for key, value in params.items() if value is not None and value != ""})
        if query:
            url = f"{url}?{query}"

    payload = None
    headers = {"accept": "application/json"}
    if body is not None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["content-type"] = "application/json"

    request = urllib.request.Request(url, data=payload, headers=headers, method=method.upper())
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise AssertionError(f"{method} {path} failed: HTTP {exc.code} {detail}") from exc
    except urllib.error.URLError as exc:
        raise AssertionError(f"{method} {path} failed: {exc}") from exc

    try:
        return json.loads(raw) if raw else None
    except json.JSONDecodeError as exc:
        raise AssertionError(f"{method} {path} returned non-JSON: {raw[:240]}") from exc


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def as_list(value: object) -> list:
    expect(isinstance(value, list), f"expected list, got {type(value).__name__}")
    return value


def as_dict(value: object) -> dict:
    expect(isinstance(value, dict), f"expected dict, got {type(value).__name__}")
    return value


def run_task(name: str, fn) -> dict:
    started = time.perf_counter()
    try:
        info = fn() or {}
        status = "passed"
        error = ""
    except Exception as exc:  # noqa: BLE001
        info = {}
        status = "failed"
        error = str(exc)
    duration_ms = round((time.perf_counter() - started) * 1000, 1)
    result = {
        "task": name,
        "status": status,
        "duration_ms": duration_ms,
    }
    if error:
        result["error"] = error
    if info:
        result["info"] = info
    return result


def choose_department(rows: list[dict]) -> dict:
    expect(rows, "department list is empty")
    ranked = sorted(rows, key=lambda item: int(item.get("occupied_count") or item.get("bed_count") or 0), reverse=True)
    return ranked[0]


def build_case_payload(department_id: str, round_index: int) -> dict:
    return {
        "patient_id": "admin-sync-test-case",
        "encounter_id": f"enc-admin-sync-{round_index}",
        "department_id": department_id,
        "bed_no": "A99",
        "room_no": "A-09",
        "full_name": f"联通回归患者{round_index:02d}",
        "mrn": f"MRN-SYNC-{round_index:02d}",
        "inpatient_no": f"IP-SYNC-{round_index:02d}",
        "gender": "女",
        "age": 48,
        "blood_type": "B+",
        "allergy_info": "青霉素过敏",
        "current_status": "admitted",
        "diagnoses": ["护理联通回归观察"],
        "risk_tags": ["回归测试"],
        "pending_tasks": [f"第{round_index}轮联通核对"],
        "latest_observations": [
            {"name": "SpO2", "value": "98%", "abnormal_flag": "normal"},
            {"name": "血压", "value": "118/72", "abnormal_flag": "normal"},
        ],
    }


def main() -> int:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    report: dict[str, object] = {
        "base_url": BASE_URL,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "rounds": [],
    }

    overall_failed = False

    for round_index in range(1, ROUND_COUNT + 1):
        ctx: dict[str, object] = {}
        round_results: list[dict] = []

        def task_health() -> dict:
            payload = as_dict(request_json("GET", "/health"))
            expect(payload.get("status") == "ok", "gateway health status is not ok")
            return {"service": payload.get("service")}

        def task_departments() -> dict:
            rows = as_list(request_json("GET", "/api/admin/departments"))
            department = choose_department([as_dict(item) for item in rows])
            ctx["department_id"] = str(department.get("id") or "")
            expect(bool(ctx["department_id"]), "department_id missing")
            return {"department_id": ctx["department_id"], "department_name": department.get("name")}

        def task_ward_analytics() -> dict:
            payload = as_dict(
                request_json(
                    "GET",
                    "/api/admin/ward-analytics",
                    params={"department_id": ctx["department_id"]},
                )
            )
            expect(str(payload.get("department_id") or "") == str(ctx["department_id"]), "analytics department mismatch")
            return {
                "critical": payload.get("critical_count"),
                "high": payload.get("high_risk_count"),
                "beds": len(payload.get("beds") or []),
            }

        def task_case_list() -> dict:
            rows = as_list(
                request_json(
                    "GET",
                    "/api/admin/patient-cases",
                    params={"department_id": ctx["department_id"], "limit": 50},
                )
            )
            expect(isinstance(rows, list), "case list is not a list")
            if rows:
                first = as_dict(rows[0])
                ctx["existing_patient_id"] = first.get("patient_id")
            return {"case_count": len(rows), "sample_patient_id": ctx.get("existing_patient_id")}

        def task_case_upsert_and_readback() -> dict:
            payload = build_case_payload(str(ctx["department_id"]), round_index)
            saved = as_dict(request_json("POST", "/api/admin/patient-cases", body=payload))
            patient = as_dict(saved.get("patient") or {})
            context = as_dict(saved.get("context") or {})
            expect(str(patient.get("id") or "") == payload["patient_id"], "saved patient id mismatch")
            expect(str(context.get("bed_no") or "") == payload["bed_no"], "saved bed number mismatch")

            patient_detail = as_dict(request_json("GET", f"/api/patients/{payload['patient_id']}"))
            case_bundle = as_dict(request_json("GET", f"/api/admin/patient-cases/{payload['patient_id']}"))
            expect(patient_detail.get("full_name") == payload["full_name"], "patient detail not synced")
            expect(as_dict(case_bundle.get("patient") or {}).get("full_name") == payload["full_name"], "admin case bundle not synced")
            ctx["test_patient_id"] = payload["patient_id"]
            return {"patient_id": payload["patient_id"], "full_name": payload["full_name"]}

        def task_document_templates() -> dict:
            rows = as_list(request_json("GET", "/api/document/templates"))
            expect(rows is not None, "template list request failed")
            return {"template_count": len(rows)}

        def task_document_history() -> dict:
            rows = as_list(request_json("GET", "/api/document/history", params={"limit": 30, "patient_id": ctx.get("test_patient_id") or ""}))
            return {"history_count": len(rows)}

        def task_account_list() -> dict:
            rows = as_list(request_json("GET", "/api/admin/accounts", params={"query": "", "status_filter": ""}))
            expect(bool(rows), "account list is empty")
            ctx["admin_accounts"] = rows
            return {"account_count": len(rows)}

        def task_account_upsert_and_login() -> dict:
            username = "admin_sync_test_user"
            payload = {
                "username": username,
                "full_name": "Admin Sync Test User",
                "role_code": "nurse",
                "department": "联通回归病区",
                "title": "测试护士",
                "phone": "13800000000",
                "status": "active",
                "password": "Pass123456",
            }
            saved = as_dict(request_json("POST", "/api/admin/accounts/upsert", body=payload))
            expect(saved.get("username") == username, "saved account username mismatch")
            login = as_dict(request_json("POST", "/api/auth/login", body={"username": username, "password": "Pass123456"}))
            expect(bool(login.get("access_token") or login.get("token")), "login token missing after account upsert")
            return {"username": username}

        def task_admin_direct_sessions() -> dict:
            user_id = "u_nurse_01"
            contact_user_id = "u_doctor_01"
            open_session = as_dict(
                request_json(
                    "POST",
                    "/api/collab/direct/open",
                    body={"user_id": user_id, "contact_user_id": contact_user_id, "patient_id": ctx.get("test_patient_id")},
                )
            )
            session_id = str(open_session.get("id") or "")
            expect(bool(session_id), "direct session id missing")
            phrase = f"admin-sync-regression-round-{round_index}"
            request_json(
                "POST",
                "/api/collab/direct/message",
                body={"session_id": session_id, "sender_id": user_id, "content": phrase},
            )
            rows = as_list(request_json("GET", "/api/admin/direct-sessions", params={"query": phrase, "limit": 20}))
            expect(rows, "admin direct session list did not return seeded session")
            detail = as_dict(request_json("GET", f"/api/admin/direct-sessions/{session_id}"))
            messages = as_list(detail.get("messages") or [])
            expect(any(phrase in str(as_dict(item).get("content") or "") for item in messages), "admin direct session detail missing seeded message")
            return {"session_id": session_id, "matched_messages": len(messages)}

        tasks = [
            ("health", task_health),
            ("admin_departments", task_departments),
            ("ward_analytics", task_ward_analytics),
            ("patient_case_list", task_case_list),
            ("patient_case_upsert_readback", task_case_upsert_and_readback),
            ("document_templates", task_document_templates),
            ("document_history", task_document_history),
            ("account_list", task_account_list),
            ("account_upsert_login", task_account_upsert_and_login),
            ("admin_direct_sessions", task_admin_direct_sessions),
        ]

        for name, fn in tasks:
            result = run_task(name, fn)
            if result["status"] != "passed":
                overall_failed = True
            round_results.append(result)

        report["rounds"].append(
            {
                "round": round_index,
                "passed": sum(1 for item in round_results if item["status"] == "passed"),
                "failed": sum(1 for item in round_results if item["status"] == "failed"),
                "results": round_results,
            }
        )

    report["summary"] = {
        "round_count": ROUND_COUNT,
        "task_count_per_round": 10,
        "failed_rounds": sum(1 for item in report["rounds"] if item["failed"]),
        "failed_tasks": sum(item["failed"] for item in report["rounds"]),
    }

    OUTPUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"report_saved {OUTPUT_PATH}")
    print(json.dumps(report["summary"], ensure_ascii=False))
    return 1 if overall_failed else 0


if __name__ == "__main__":
    sys.exit(main())
