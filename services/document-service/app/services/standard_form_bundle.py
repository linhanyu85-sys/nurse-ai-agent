from __future__ import annotations

from typing import Any

from app.services.standard_forms import (
    get_field_schema,
    get_standard_form_definition,
    list_standard_form_definitions,
    normalize_document_type,
)


def _map_item_type(input_type: str | None) -> str:
    token = str(input_type or "text").strip().lower()
    if token in {"number", "integer"}:
        return "integer"
    if token in {"decimal", "float"}:
        return "decimal"
    if token == "date":
        return "date"
    if token == "datetime":
        return "dateTime"
    if token in {"choice", "select"}:
        return "choice"
    return "string"


def _sheet_columns(document_type: str) -> list[dict[str, Any]]:
    columns: list[dict[str, Any]] = []
    for field in get_field_schema(document_type):
        columns.append(
            {
                "key": str(field.get("key") or ""),
                "label": str(field.get("label") or field.get("key") or ""),
                "section": str(field.get("section") or "文书内容"),
                "required": bool(field.get("required")),
                "input_type": str(field.get("input_type") or "text"),
            }
        )
    return columns


def build_standard_form_questionnaire(document_type: str) -> dict[str, Any]:
    normalized_type = normalize_document_type(document_type)
    standard = get_standard_form_definition(normalized_type)
    columns = _sheet_columns(normalized_type)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for column in columns:
        grouped.setdefault(str(column["section"]), []).append(column)

    items: list[dict[str, Any]] = []
    for section in standard.get("sections", []):
        title = str(section.get("title") or "文书内容")
        section_key = str(section.get("key") or title)
        fields = grouped.get(title, [])
        if not fields:
          continue
        items.append(
            {
                "linkId": section_key,
                "text": title,
                "type": "group",
                "item": [
                    {
                        "linkId": str(field["key"]),
                        "text": str(field["label"]),
                        "type": _map_item_type(str(field.get("input_type") or "text")),
                        "required": bool(field.get("required")),
                        "extension": [
                            {
                                "url": "http://hl7.org/fhir/StructureDefinition/questionnaire-itemControl",
                                "valueCodeableConcept": {
                                    "text": "multiline" if str(field.get("input_type") or "") == "textarea" else "text-box"
                                },
                            }
                        ],
                    }
                    for field in fields
                ],
            }
        )

    return {
        "resourceType": "Questionnaire",
        "id": standard["id"],
        "url": f"https://ai-nursing.local/fhir/Questionnaire/{standard['id']}",
        "version": str(standard.get("schema_version") or "1.0.0"),
        "name": normalized_type,
        "title": standard["name"],
        "status": "active",
        "experimental": True,
        "publisher": "临床 AI 护理精细化系统",
        "description": standard.get("description"),
        "subjectType": ["Patient"],
        "meta": {
            "profile": [
                "http://hl7.org/fhir/uv/sdc/StructureDefinition/sdc-questionnaire",
            ]
        },
        "item": items,
    }


def build_standard_form_bundle(document_type: str) -> dict[str, Any]:
    normalized_type = normalize_document_type(document_type)
    standard = get_standard_form_definition(normalized_type)
    columns = _sheet_columns(normalized_type)
    return {
        "document_type": normalized_type,
        "form_id": standard["id"],
        "name": standard["name"],
        "standard_family": standard.get("standard_family"),
        "description": standard.get("description"),
        "schema_version": standard.get("schema_version"),
        "source_refs": list(standard.get("source_refs") or []),
        "sections": list(standard.get("sections") or []),
        "field_count": len(columns),
        "sheet_columns": columns,
        "questionnaire": build_standard_form_questionnaire(normalized_type),
    }


def list_standard_form_bundles() -> list[dict[str, Any]]:
    bundles: list[dict[str, Any]] = []
    for definition in list_standard_form_definitions():
        document_type = str(definition.get("document_type") or "")
        if document_type:
            bundles.append(build_standard_form_bundle(document_type))
    return bundles
