from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT_DIR / "logs"
CONTAINER_NAME = "ai_nursing_postgres"
PSQL_BASE = [
    "docker",
    "exec",
    "-i",
    CONTAINER_NAME,
    "psql",
    "-U",
    "postgres",
    "-d",
    "ai_nursing",
    "-X",
    "-A",
    "-t",
    "-v",
    "ON_ERROR_STOP=1",
    "-f",
    "-",
]

BAD_STATUS_PATTERN = re.compile(r"文书状态：草稿（[^）]+）[、，,\s]*")
BAD_PLACEHOLDER_PATTERN = re.compile(r"\{\{\s*[^{}]+\s*\}\}")
BAD_NAME_PATTERN = re.compile(r"姓名：\s*(?:\?{3,}|-+)")
BAD_BED_PATTERN = re.compile(r"床号：\s*(?:\?{3,}|-+)")
BAD_MRN_PATTERN = re.compile(r"病案号：\s*(?:\?{3,}|-+)")
BAD_IP_PATTERN = re.compile(r"住院号：\s*(?:\?{3,}|-+)")
BAD_DEPT_PATTERN = re.compile(r"科别：\s*(?:\?{3,}|-+|待补充)")
BAD_NOTE_PATTERN = re.compile(r"特殊情况记录：\s*(?:\?{3,}|-+)")
BAD_AI_HINT_PATTERN = re.compile(r"(?:\n|^)\s*(?:\[?AI提示\]?[:：]?).*(?=\n|$)")


DOCUMENT_QUERY = """
SELECT
  d.id::text AS id,
  d.patient_id::text AS patient_id,
  COALESCE(d.encounter_id::text, '') AS encounter_id,
  d.document_type,
  d.draft_text,
  d.structured_fields,
  d.status,
  d.source_type,
  COALESCE(p.full_name, '') AS patient_name,
  COALESCE(p.gender, '') AS gender,
  COALESCE(p.age::text, '') AS age,
  COALESCE(p.mrn, '') AS mrn,
  COALESCE(p.inpatient_no, '') AS inpatient_no,
  COALESCE(b.bed_no, '') AS bed_no,
  COALESCE(dep.name, '') AS department_name,
  COALESCE(e.admission_diagnosis, '') AS admission_diagnosis,
  d.created_at,
  d.updated_at
FROM document_drafts d
LEFT JOIN patients p ON p.id = d.patient_id
LEFT JOIN LATERAL (
  SELECT *
  FROM encounters e0
  WHERE e0.id = d.encounter_id OR e0.patient_id = d.patient_id
  ORDER BY CASE WHEN e0.id = d.encounter_id THEN 0 ELSE 1 END, e0.updated_at DESC, e0.created_at DESC
  LIMIT 1
) e ON TRUE
LEFT JOIN LATERAL (
  SELECT *
  FROM beds b0
  WHERE b0.current_patient_id = d.patient_id
  ORDER BY b0.updated_at DESC, b0.created_at DESC
  LIMIT 1
) b ON TRUE
LEFT JOIN departments dep ON dep.id = COALESCE(e.department_id, b.department_id)
WHERE
  d.draft_text LIKE '%{{%'
  OR d.draft_text LIKE '%}}%'
  OR d.draft_text LIKE '%???%'
  OR d.draft_text LIKE '%患者ID: 33333333-%'
  OR d.draft_text LIKE '[nursing_note] %'
  OR d.draft_text LIKE '%姓名：-%'
  OR d.draft_text LIKE '%姓名：???%'
  OR d.draft_text LIKE '%床号：-%'
  OR d.draft_text LIKE '%病案号：-%'
  OR d.draft_text LIKE '%住院号：-%'
  OR d.draft_text LIKE '%AI提示:%'
  OR d.draft_text LIKE '%文书状态：草稿（%'
ORDER BY d.updated_at DESC
"""

RECOMMENDATION_QUERY = """
SELECT
  r.id::text AS id,
  r.patient_id::text AS patient_id,
  COALESCE(r.encounter_id::text, '') AS encounter_id,
  r.scenario,
  r.summary,
  r.findings,
  r.recommendations,
  COALESCE(p.full_name, '') AS patient_name,
  COALESCE(p.mrn, '') AS mrn,
  COALESCE(b.bed_no, '') AS bed_no,
  COALESCE(e.admission_diagnosis, '') AS admission_diagnosis,
  r.created_at
FROM ai_recommendations r
LEFT JOIN patients p ON p.id = r.patient_id
LEFT JOIN LATERAL (
  SELECT *
  FROM encounters e0
  WHERE e0.id = r.encounter_id OR e0.patient_id = r.patient_id
  ORDER BY CASE WHEN e0.id = r.encounter_id THEN 0 ELSE 1 END, e0.updated_at DESC, e0.created_at DESC
  LIMIT 1
) e ON TRUE
LEFT JOIN LATERAL (
  SELECT *
  FROM beds b0
  WHERE b0.current_patient_id = r.patient_id
  ORDER BY b0.updated_at DESC, b0.created_at DESC
  LIMIT 1
) b ON TRUE
WHERE
  r.summary LIKE '%{{%'
  OR r.summary LIKE '%}}%'
  OR r.summary LIKE '%???%'
  OR r.summary LIKE '{%'
  OR r.summary LIKE '%"task"%'
  OR r.summary LIKE '%"question"%'
ORDER BY r.created_at DESC
"""


def run_psql(sql: str) -> str:
    proc = subprocess.run(
        PSQL_BASE,
        input=sql,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(ROOT_DIR),
        check=False,
    )
    if proc.returncode != 0:
        message = (proc.stderr or proc.stdout).strip()
        raise RuntimeError(message or "psql execution failed")
    return proc.stdout.strip()


def query_json(sql: str) -> list[dict[str, Any]]:
    wrapped = f"SELECT COALESCE(json_agg(t), '[]'::json)::text FROM ({sql}) t;\n"
    raw = run_psql(wrapped)
    return json.loads(raw) if raw else []


def sql_literal(value: Any) -> str:
    if value is None:
        return "NULL"
    text = str(value).replace("'", "''")
    return f"'{text}'"


def json_literal(value: Any) -> str:
    return sql_literal(json.dumps(value, ensure_ascii=False))


def compact_space(value: str) -> str:
    text = str(value or "").replace("\r", "")
    text = BAD_STATUS_PATTERN.sub("", text)
    text = BAD_AI_HINT_PATTERN.sub("", text)
    text = BAD_PLACEHOLDER_PATTERN.sub("", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def mostly_garbled(value: str) -> bool:
    visible = re.sub(r"\s+", "", str(value or ""))
    if len(visible) < 4:
        return False
    bad_count = len(re.findall(r"[?？�]", visible))
    return bad_count * 10 >= len(visible) * 4


def clean_string(value: Any) -> str:
    text = compact_space(str(value or ""))
    return text.strip(" ，。；、")


def clean_list(value: Any) -> list[str]:
    if isinstance(value, list):
        raw_items = value
    elif value is None:
        raw_items = []
    else:
        raw_items = [value]
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        text = clean_string(item)
        if not text:
            continue
        if mostly_garbled(text):
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(text)
    return cleaned


def first_present(*values: Any) -> str:
    for value in values:
        if isinstance(value, list):
            cleaned_list = clean_list(value)
            if cleaned_list:
                return "；".join(cleaned_list)
            continue
        text = clean_string(value)
        if not text:
            continue
        if text in {"-", "待补充", "暂无", "???"}:
            continue
        if BAD_PLACEHOLDER_PATTERN.search(text):
            continue
        if mostly_garbled(text):
            continue
        return text
    return ""


def parse_label(text: str, label: str) -> str:
    pattern = rf"{re.escape(label)}[:：]\s*(.+?)(?=\s+[^\s:：]+[:：]|$)"
    match = re.search(pattern, text)
    return clean_string(match.group(1)) if match else ""


def normalize_pending_tasks(value: Any) -> list[str]:
    items = clean_list(value)
    output: list[str] = []
    seen: set[str] = set()
    for item in items:
        cleaned = BAD_STATUS_PATTERN.sub("", item).strip(" ，。；、")
        if not cleaned or mostly_garbled(cleaned):
            continue
        key = cleaned.casefold()
        if key in seen:
            continue
        seen.add(key)
        output.append(cleaned)
    return output


def normalize_text_field(value: Any, *, fallback: str = "") -> str:
    text = clean_string(value)
    if not text or BAD_PLACEHOLDER_PATTERN.search(text) or mostly_garbled(text):
        return fallback
    if re.fullmatch(r"[-?？]+", text):
        return fallback
    return text


def normalize_structured_fields(raw: Any, *, patient_name: str, bed_no: str, mrn: str, inpatient_no: str, note_text: str) -> dict[str, Any]:
    fields = raw if isinstance(raw, dict) else {}

    def walk(node: Any, path: tuple[str, ...] = ()) -> Any:
        if isinstance(node, dict):
            return {key: walk(value, (*path, str(key))) for key, value in node.items()}
        if isinstance(node, list):
            items = [walk(item, path) for item in node]
            return [item for item in items if item not in ("", None, [], {})]
        if not isinstance(node, str):
            return node

        key = path[-1].lower() if path else ""
        text = clean_string(node)
        if "文书状态：草稿" in text:
            text = BAD_STATUS_PATTERN.sub("", text).strip(" ，。；、")
        if not text:
            return ""
        if BAD_PLACEHOLDER_PATTERN.search(text) or mostly_garbled(text) or re.fullmatch(r"[-?？]+", text):
            if key in {"patient_name", "full_name", "name"}:
                return patient_name
            if key in {"bed_no", "bed", "bedno"}:
                return bed_no
            if key == "mrn":
                return mrn
            if key == "inpatient_no":
                return inpatient_no
            if key in {"special_notes", "spoken_text"}:
                return note_text
            return ""
        return text

    next_fields = walk(fields)
    if isinstance(next_fields, dict):
        return next_fields
    return {}


def build_document_template(row: dict[str, Any], *, diagnosis: str, risk_tags: str, pending_tasks: str, note_text: str) -> str:
    gender = first_present(row.get("gender"), "待补充")
    age = first_present(row.get("age"), "待补充")
    department_name = first_present(row.get("department_name"), "待补充")
    patient_name = first_present(row.get("patient_name"), "待补充")
    bed_no = first_present(row.get("bed_no"), "待补充")
    mrn = first_present(row.get("mrn"), "待补充")
    inpatient_no = first_present(row.get("inpatient_no"), "待补充")
    diagnosis_text = first_present(diagnosis, "待补充")
    risk_text = first_present(risk_tags, "暂无")
    pending_text = first_present(pending_tasks, "待补充")
    note = first_present(note_text, "待补充")
    return "\n".join(
        [
            "【护理记录单】",
            f"姓名：{patient_name}",
            f"性别：{gender}",
            f"年龄：{age}",
            f"科别：{department_name}",
            f"床号：{bed_no}",
            f"病案号：{mrn}",
            f"住院号：{inpatient_no}",
            f"主要诊断：{diagnosis_text}",
            f"风险标签：{risk_text}",
            f"待处理任务：{pending_text}",
            f"特殊情况记录：{note}",
        ]
    )


def sanitize_document_row(row: dict[str, Any]) -> dict[str, Any] | None:
    draft_text = str(row.get("draft_text") or "")
    structured_fields = row.get("structured_fields") if isinstance(row.get("structured_fields"), dict) else {}

    diagnoses = first_present(
        structured_fields.get("diagnoses"),
        parse_label(draft_text, "主要诊断"),
        row.get("admission_diagnosis"),
    )
    risk_tags = first_present(
        structured_fields.get("risk_tags"),
        parse_label(draft_text, "风险标签"),
        "暂无",
    )
    pending_tasks = "；".join(
        normalize_pending_tasks(
            structured_fields.get("pending_tasks")
            or parse_label(draft_text, "待处理任务")
        )
    )
    note_text = first_present(
        structured_fields.get("special_notes"),
        structured_fields.get("spoken_text"),
        parse_label(draft_text, "特殊情况记录"),
        parse_label(draft_text, "护理记录"),
    )

    patient_name = first_present(row.get("patient_name"), structured_fields.get("patient_name"), structured_fields.get("full_name"), structured_fields.get("name"))
    bed_no = first_present(row.get("bed_no"), structured_fields.get("bed_no"), structured_fields.get("bed"))
    mrn = first_present(row.get("mrn"), structured_fields.get("mrn"))
    inpatient_no = first_present(row.get("inpatient_no"), structured_fields.get("inpatient_no"))

    next_text = compact_space(draft_text)
    should_rebuild = draft_text.startswith("[nursing_note]") or "33333333-" in draft_text
    should_rebuild = should_rebuild or BAD_PLACEHOLDER_PATTERN.search(next_text) is not None
    should_rebuild = should_rebuild or mostly_garbled(next_text)

    if should_rebuild:
        next_text = build_document_template(
            row,
            diagnosis=diagnoses,
            risk_tags=risk_tags,
            pending_tasks=pending_tasks,
            note_text=note_text,
        )
    else:
        replacements = {
            "name": first_present(patient_name, "待补充"),
            "bed": first_present(bed_no, "待补充"),
            "mrn": first_present(mrn, "待补充"),
            "ip": first_present(inpatient_no, "待补充"),
            "dept": first_present(row.get("department_name"), "待补充"),
        }
        next_text = BAD_NAME_PATTERN.sub(f"姓名：{replacements['name']}", next_text)
        next_text = BAD_BED_PATTERN.sub(f"床号：{replacements['bed']}", next_text)
        next_text = BAD_MRN_PATTERN.sub(f"病案号：{replacements['mrn']}", next_text)
        next_text = BAD_IP_PATTERN.sub(f"住院号：{replacements['ip']}", next_text)
        next_text = BAD_DEPT_PATTERN.sub(f"科别：{replacements['dept']}", next_text)
        if BAD_NOTE_PATTERN.search(next_text):
            next_note = note_text or "待补充"
            next_text = BAD_NOTE_PATTERN.sub(f"特殊情况记录：{next_note}", next_text)
        current_note = parse_label(next_text, "特殊情况记录")
        if current_note and mostly_garbled(current_note):
            next_note = note_text if note_text and not mostly_garbled(note_text) else "待补充"
            next_text = re.sub(r"(特殊情况记录[:：])\s*[^\n]*", rf"\1{next_note}", next_text)
        next_text = compact_space(next_text)

    next_fields = normalize_structured_fields(
        structured_fields,
        patient_name=patient_name,
        bed_no=bed_no,
        mrn=mrn,
        inpatient_no=inpatient_no,
        note_text=note_text,
    )
    pending_task_list = normalize_pending_tasks(next_fields.get("pending_tasks"))
    if pending_task_list:
        next_fields["pending_tasks"] = pending_task_list
    if diagnoses and not clean_list(next_fields.get("diagnoses")):
        next_fields["diagnoses"] = [diagnoses]
    if risk_tags and not clean_list(next_fields.get("risk_tags")):
        next_fields["risk_tags"] = [risk_tags]
    if note_text:
        if not normalize_text_field(next_fields.get("spoken_text")):
            next_fields["spoken_text"] = note_text
        if "special_notes" in next_fields and not normalize_text_field(next_fields.get("special_notes")):
            next_fields["special_notes"] = note_text
    if patient_name:
        next_fields["patient_name"] = patient_name
        next_fields["full_name"] = patient_name
    if bed_no:
        next_fields["bed_no"] = bed_no
    if mrn:
        next_fields["mrn"] = mrn
    if inpatient_no:
        next_fields["inpatient_no"] = inpatient_no

    if next_text == draft_text and next_fields == structured_fields:
        return None
    return {
        "id": row["id"],
        "draft_text": next_text,
        "structured_fields": next_fields,
    }


def sanitize_recommendation_row(row: dict[str, Any]) -> dict[str, Any] | None:
    findings = clean_list(row.get("findings"))
    recommendations_raw = row.get("recommendations") if isinstance(row.get("recommendations"), list) else []
    recommendations: list[dict[str, Any]] = []
    for item in recommendations_raw:
        if not isinstance(item, dict):
            continue
        title = clean_string(item.get("title"))
        rationale = clean_string(item.get("rationale"))
        if title:
            next_item = dict(item)
            next_item["title"] = title
            if rationale:
                next_item["rationale"] = rationale
            recommendations.append(next_item)

    patient_name = first_present(row.get("patient_name"), "患者")
    bed_no = first_present(row.get("bed_no"))
    diagnosis = first_present(row.get("admission_diagnosis"), "请结合最新病情继续评估")
    lead = f"{bed_no}床{patient_name}" if bed_no else patient_name
    primary_finding = first_present(findings[0] if findings else "", "请复核最新生命体征与关键异常指标")
    primary_action = first_present(
        recommendations[0].get("title") if recommendations else "",
        "高优先级处置并持续复核",
    )
    next_summary = f"{lead}当前重点：{diagnosis}；首要异常：{primary_finding}。建议先执行{primary_action}。"
    current_summary = clean_string(row.get("summary"))

    if current_summary == next_summary and findings == row.get("findings") and recommendations == row.get("recommendations"):
        return None
    return {
        "id": row["id"],
        "summary": next_summary,
        "findings": findings,
        "recommendations": recommendations,
    }


def build_update_sql(document_updates: list[dict[str, Any]], recommendation_updates: list[dict[str, Any]]) -> str:
    statements: list[str] = ["BEGIN;"]

    for item in document_updates:
        statements.append(
            "\n".join(
                [
                    "UPDATE document_drafts",
                    f"SET draft_text = {sql_literal(item['draft_text'])},",
                    f"    structured_fields = {json_literal(item['structured_fields'])}::jsonb",
                    f"WHERE id = {sql_literal(item['id'])}::uuid;",
                ]
            )
        )

    for item in recommendation_updates:
        statements.append(
            "\n".join(
                [
                    "UPDATE ai_recommendations",
                    f"SET summary = {sql_literal(item['summary'])},",
                    f"    findings = {json_literal(item['findings'])}::jsonb,",
                    f"    recommendations = {json_literal(item['recommendations'])}::jsonb",
                    f"WHERE id = {sql_literal(item['id'])}::uuid;",
                ]
            )
        )

    statements.extend(
        [
            "ANALYZE patients;",
            "ANALYZE encounters;",
            "ANALYZE beds;",
            "ANALYZE document_drafts;",
            "ANALYZE ai_recommendations;",
            "ANALYZE care_tasks;",
            "ANALYZE collaboration_threads;",
            "ANALYZE collaboration_messages;",
            "ANALYZE users;",
            "COMMIT;",
        ]
    )
    return "\n\n".join(statements) + "\n"


def collect_bad_counts() -> dict[str, int]:
    sql = """
    SELECT json_build_object(
      'document_bad_rows',
      (SELECT COUNT(*) FROM document_drafts
       WHERE draft_text LIKE '%{{%'
          OR draft_text LIKE '%}}%'
          OR draft_text LIKE '%???%'
          OR draft_text LIKE '%患者ID: 33333333-%'
          OR draft_text LIKE '[nursing_note] %'
          OR draft_text LIKE '%姓名：-%'
          OR draft_text LIKE '%姓名：???%'
          OR draft_text LIKE '%床号：-%'
          OR draft_text LIKE '%病案号：-%'
          OR draft_text LIKE '%住院号：-%'
          OR draft_text LIKE '%AI提示:%'
          OR draft_text LIKE '%文书状态：草稿（%'),
      'recommendation_bad_rows',
      (SELECT COUNT(*) FROM ai_recommendations
       WHERE summary LIKE '%{{%'
          OR summary LIKE '%}}%'
          OR summary LIKE '%???%'
          OR summary LIKE '{%'
          OR summary LIKE '%"task"%'
          OR summary LIKE '%"question"%')
    )::text;
    """
    raw = run_psql(sql)
    return json.loads(raw) if raw else {"document_bad_rows": 0, "recommendation_bad_rows": 0}


def main() -> int:
    parser = argparse.ArgumentParser(description="Normalize deployment data in local Postgres.")
    parser.add_argument("--dry-run", action="store_true", help="Inspect and write reports without applying updates.")
    args = parser.parse_args()

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = LOG_DIR / f"deploy_db_normalization_backup_{timestamp}.json"
    report_path = LOG_DIR / "deploy_db_normalization_report.json"

    before = collect_bad_counts()
    document_rows = query_json(DOCUMENT_QUERY)
    recommendation_rows = query_json(RECOMMENDATION_QUERY)

    document_updates = [item for item in (sanitize_document_row(row) for row in document_rows) if item]
    recommendation_updates = [item for item in (sanitize_recommendation_row(row) for row in recommendation_rows) if item]

    backup_payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "document_drafts": document_rows,
        "ai_recommendations": recommendation_rows,
    }
    backup_path.write_text(json.dumps(backup_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    if not args.dry_run and (document_updates or recommendation_updates):
        run_psql(build_update_sql(document_updates, recommendation_updates))

    after = collect_bad_counts() if not args.dry_run else before
    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "dry_run": bool(args.dry_run),
        "backup_path": str(backup_path),
        "before": before,
        "after": after,
        "document_rows_scanned": len(document_rows),
        "recommendation_rows_scanned": len(recommendation_rows),
        "document_rows_updated": len(document_updates),
        "recommendation_rows_updated": len(recommendation_updates),
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
