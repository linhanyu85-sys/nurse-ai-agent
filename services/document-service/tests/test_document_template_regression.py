from __future__ import annotations

import asyncio
from copy import deepcopy

import pytest

from app.core.config import settings
from app.services.generator import build_document_draft
from app.services.llm_client import MANUAL_REVIEW_NOTICE
from app.services.standard_forms import normalize_document_type
from app.services.store import document_store


TEMPLATE_BY_TYPE = {
    item.document_type: item
    for item in document_store.list_templates()
    if item.document_type
}

BASE_CONTEXT = {
    "patient_id": "pat-regression-001",
    "patient_name": "王淑华",
    "full_name": "王淑华",
    "gender": "女",
    "age": 68,
    "department_name": "神经内科",
    "ward_name": "一病区",
    "bed_no": "12",
    "mrn": "MRN-20260404-01",
    "inpatient_no": "ZYH-20260404-01",
    "chart_date": "2026-04-04",
    "shift_date": "2026-04-04",
    "shift_type": "白班",
    "current_time": "2026-04-04 09:30",
    "admission_date": "2026-04-02",
    "diagnoses": ["脑梗死恢复期", "高血压"],
    "risk_level": "中风险",
    "risk_tags": ["跌倒风险", "压疮风险"],
    "pending_tasks": ["继续监测生命体征", "落实翻身护理"],
    "latest_observations": [
        {"name": "血压", "value": "148/86mmHg"},
        {"name": "脉搏", "value": "82次/分"},
        {"name": "体温", "value": "36.8℃"},
    ],
    "temperature_value": "36.8",
    "pulse_value": "82",
    "heart_rate_value": "84",
    "respiratory_rate": "18",
    "blood_pressure": "148/86",
    "spo2_value": "97",
    "cvp_value": "8",
    "blood_glucose_value": "7.2",
    "pain_score": "2",
    "intake_total": "1200",
    "output_total": "900",
    "intake_summary": "静脉补液 500ml，口服水 700ml",
    "output_summary": "尿量 900ml",
    "stool_count": "1",
    "requested_by": "u_linmeili",
    "supervisor_sign": "赵丽",
}


def _scenario_context(name: str) -> dict:
    context = deepcopy(BASE_CONTEXT)

    if name == "worsening":
        context.update(
            {
                "risk_level": "高风险",
                "risk_tags": ["跌倒高风险", "误吸风险", "压疮高风险"],
                "pending_tasks": ["严密观察意识变化", "复测血压", "保持床栏抬起"],
                "latest_observations": [
                    {"name": "血压", "value": "168/98mmHg", "abnormal_flag": "偏高"},
                    {"name": "SpO2", "value": "93%", "abnormal_flag": "偏低"},
                ],
                "temperature_value": "37.6",
                "pulse_value": "96",
                "respiratory_rate": "24",
                "spo2_value": "93",
                "pain_score": "4",
            }
        )
    elif name == "postop":
        context.update(
            {
                "department_name": "骨科",
                "diagnoses": ["股骨颈骨折术后"],
                "operation_name": "股骨颈骨折内固定术",
                "post_op_day": "1",
                "hospital_day": "3",
                "puncture_site": "右上肢静脉",
                "position_fixation": "平卧位，患肢外展中立位固定",
                "drainage_status": "切口引流管 1 根，通畅",
            }
        )
    elif name == "transfusion":
        context.update(
            {
                "department_name": "血液科",
                "ward_name": "二病区",
                "blood_type": "A",
                "rh_type": "阳性",
                "transfusion_priority": "常规",
                "donor_barcode": "BC20260404001",
                "blood_component": "悬浮红细胞 2U",
                "blood_volume": "400",
                "transfusion_start_time": "2026-04-04 10:00",
                "transfusion_end_time": "2026-04-04 12:05",
                "double_checker": "u_wangjing",
                "temperature_before": "36.7",
                "blood_pressure_before": "132/78",
                "temperature_after": "36.9",
                "blood_pressure_after": "134/80",
                "actual_transfusion_volume": "400ml",
                "precheck_summary": "双人核对姓名、血型、血袋编码及交叉配血结果一致。",
                "transfusion_reaction_history": "既往无输血不良反应史",
                "spoken_text": "输血过程平稳，无寒战、发热、皮疹等不良反应。",
                "transfusion_reaction": "无",
                "first_stage_rate": "2ml/min",
                "first_stage_reaction": "无",
                "second_stage_rate": "4ml/min",
                "second_stage_reaction": "无",
            }
        )
    elif name == "handover":
        context.update(
            {
                "department_name": "综合病区",
                "shift_type": "夜班",
                "ward_total": "42",
                "discharge_count": "3",
                "transfer_out_count": "1",
                "admission_count": "4",
                "transfer_in_count": "2",
                "operation_count": "2",
                "tomorrow_operation_count": "1",
                "serious_count": "2",
                "critical_count": "1",
                "death_count": "0",
                "special_disease_care": "糖尿病足换药患者 2 人",
                "other_summary": "一名患者需陪检 MRI",
                "infectious_report": "无新增报卡",
                "special_events": "16床夜间血压波动，已通知医生。",
                "receiver_sign": "u_chenhua",
            }
        )
    elif name == "glucose":
        context.update(
            {
                "department_name": "内分泌科",
                "blood_glucose_value": "10.8",
                "glucose_value": "10.8",
                "breakfast_before_glucose": "8.6",
                "breakfast_after_glucose": "10.2",
                "lunch_before_glucose": "7.9",
                "lunch_after_glucose": "9.8",
                "dinner_before_glucose": "7.5",
                "dinner_after_glucose": "10.1",
                "bedtime_glucose": "8.2",
            }
        )
    elif name == "stroke_tcm":
        context.update(
            {
                "department_name": "中医脑病科",
                "patient_identifier": "PAT-TCM-01",
                "education_level": "高中",
                "phone": "13800000000",
                "yang_pattern": "风火上扰清窍",
                "yin_pattern": "气虚血瘀",
                "collapse_pattern": "无",
                "symptom_name": "意识障碍",
                "nursing_method": "抬高床头、密切观察意识变化、保持呼吸道通畅。",
                "tcm_technique": "穴位按压、耳穴压豆",
                "severity_before_date": "2026-04-03",
                "severity_before_score": "4",
                "severity_after_date": "2026-04-04",
                "severity_after_score": "2",
                "effect_evaluation": "意识状态较前改善。",
            }
        )
    elif name == "reaction":
        context.update(
            {
                "department_name": "血液科",
                "blood_type": "O",
                "rh_type": "阳性",
                "transfusion_priority": "紧急",
                "donor_barcode": "BC20260404009",
                "blood_component": "血浆 200ml",
                "blood_volume": "200",
                "transfusion_start_time": "2026-04-04 15:00",
                "transfusion_end_time": "2026-04-04 15:50",
                "spoken_text": "输血 15 分钟后出现寒战、皮肤瘙痒，已立即减慢滴速并报告医生。",
                "transfusion_reaction": "疑似轻度输血反应，已暂停观察。",
                "first_stage_rate": "2ml/min",
                "first_stage_reaction": "寒战、瘙痒",
                "second_stage_rate": "暂停",
                "second_stage_reaction": "症状缓解中",
            }
        )
    elif name == "count_discrepancy":
        context.update(
            {
                "department_name": "手术室",
                "operation_name": "阑尾切除术",
                "integrity_check": "术毕首次核对纱布少 1 块，复查后在器械台下找到并补记。",
                "instrument_count": "器械 18 件，术前术后相符。",
                "dressing_count": "纱布 20 块，补核后相符。",
                "special_item_count": "刀片 2 片，缝针 3 枚。",
                "special_record": "清点异常已复核并完成闭环记录。",
            }
        )

    return context


PRIMARY_SCENARIOS = ["routine", "worsening", "postop", "transfusion", "handover"]
EXTRA_CASES = [
    ("stroke_tcm_nursing_effect_evaluation", "stroke_tcm"),
    ("glucose_record", "glucose"),
    ("nursing_handover_report", "handover"),
    ("transfusion_process_record", "reaction"),
    ("surgical_count_record", "count_discrepancy"),
]

CASES: list[tuple[str, str]] = []
for document_type in TEMPLATE_BY_TYPE:
    for scenario in PRIMARY_SCENARIOS:
        CASES.append((document_type, scenario))
CASES.extend(EXTRA_CASES)

assert len(CASES) == 50


@pytest.mark.parametrize(
    ("document_type", "scenario"),
    CASES,
    ids=[f"{document_type}-{scenario}" for document_type, scenario in CASES],
)
def test_document_template_fill_regression(document_type: str, scenario: str) -> None:
    settings.local_llm_enabled = False
    settings.local_only_mode = True

    template = TEMPLATE_BY_TYPE[document_type]
    context = _scenario_context(scenario)
    spoken_text = str(context.get("spoken_text") or f"{getattr(template, 'name', document_type)} 自动回填测试")

    draft_text, structured_fields = asyncio.run(
        build_document_draft(
            document_type=document_type,
            spoken_text=spoken_text,
            context=context,
            template_text=template.template_text,
            template_name=template.name,
        )
    )

    assert draft_text
    assert MANUAL_REVIEW_NOTICE in draft_text
    assert "{{" not in draft_text and "}}" not in draft_text
    assert structured_fields["template_applied"] is True
    assert structured_fields["template_name"] == template.name
    assert structured_fields["document_type"] == document_type
    assert structured_fields["standardized_format"] is True
    assert structured_fields["editable"] is True
    assert isinstance(structured_fields.get("editable_blocks"), list)
    assert isinstance(structured_fields.get("sections"), list)
    assert structured_fields["field_summary"]["total"] == len(structured_fields["editable_blocks"])
    assert structured_fields["field_summary"]["missing"] >= 0
    assert context["patient_name"] in draft_text
    editable_keys = {str(item.get("key") or "") for item in structured_fields["editable_blocks"]}
    if "bed_no" in editable_keys:
        assert str(context["bed_no"]) in draft_text
    assert template.name.split("（")[0].split("(")[0] in draft_text


def test_imported_template_inventory_is_complete() -> None:
    expected = {
        "nursing_note",
        "temperature_chart",
        "surgical_count_record",
        "critical_patient_nursing_record",
        "transfusion_nursing_record",
        "transfusion_process_record",
        "glucose_record",
        "nursing_handover_report",
        "stroke_tcm_nursing_effect_evaluation",
    }
    assert set(TEMPLATE_BY_TYPE) == expected


def test_preferred_template_is_system_standard() -> None:
    for document_type in TEMPLATE_BY_TYPE:
        template = document_store.get_preferred_template(document_type)
        assert template is not None
        assert template.document_type == document_type
        assert template.source_type == "system"


def test_build_document_draft_locks_standard_template_when_no_template_is_passed() -> None:
    settings.local_llm_enabled = False
    settings.local_only_mode = True

    draft_text, structured_fields = asyncio.run(
        build_document_draft(
            document_type="nursing_note",
            spoken_text="患者病情平稳，继续生命体征监测并完成翻身护理。",
            context=_scenario_context("routine"),
        )
    )

    assert draft_text
    assert structured_fields["template_applied"] is True
    assert structured_fields["template_locked"] is True
    assert structured_fields["template_source_policy"] == "system_standard_locked"
    assert structured_fields["standardized_format"] is True


def test_normalize_document_type_supports_chinese_labels() -> None:
    assert normalize_document_type("\u62A4\u7406\u8BB0\u5F55\u5355") == "nursing_note"
    assert normalize_document_type("\u75C5\u91CD\uFF08\u75C5\u5371\uFF09\u62A4\u7406\u8BB0\u5F55\u5355") == "critical_patient_nursing_record"
    assert normalize_document_type("\u8F93\u8840\u8BB0\u5F55\u5355") == "transfusion_nursing_record"


def test_observation_backfill_avoids_duplicate_units_in_rendered_document() -> None:
    settings.local_llm_enabled = False
    settings.local_only_mode = True

    context = _scenario_context("routine")
    context["latest_observations"] = [
        {"name": "SpO2", "value": "89%"},
        {"name": "\u5FC3\u7387", "value": "112 \u6B21/\u5206"},
        {"name": "\u547C\u5438", "value": "24 \u6B21/\u5206"},
        {"name": "\u8840\u538B", "value": "148/86mmHg"},
    ]
    for key in ["spo2_value", "pulse_value", "heart_rate_value", "respiratory_rate", "blood_pressure"]:
        context.pop(key, None)

    draft_text, _ = asyncio.run(
        build_document_draft(
            document_type="nursing_note",
            spoken_text="\u60A3\u8005\u9700\u52A0\u5F3A\u547C\u5438\u76D1\u6D4B\u4E0E\u4F4E\u6C27\u98CE\u9669\u89C2\u5BDF\u3002",
            context=context,
        )
    )

    assert "SpO2\uff1a89%" in draft_text
    assert "P\uff1a112\u6B21/\u5206" in draft_text
    assert "HR\uff1a112\u6B21/\u5206" in draft_text
    assert "R\uff1a24\u6B21/\u5206" in draft_text
    assert "BP\uff1a148/86mmHg" in draft_text
    assert "%%" not in draft_text
    assert "\u6B21/\u5206\u6B21/\u5206" not in draft_text
    assert "mmHgmmHg" not in draft_text


def test_build_document_draft_uses_correct_system_template_for_chinese_document_name() -> None:
    settings.local_llm_enabled = False
    settings.local_only_mode = True

    draft_text, structured_fields = asyncio.run(
        build_document_draft(
            document_type="\u75C5\u91CD\uFF08\u75C5\u5371\uFF09\u62A4\u7406\u8BB0\u5F55\u5355",
            spoken_text="\u60A3\u8005\u4F4E\u6C27\u98CE\u9669\u660E\u663E\uFF0C\u9700\u52A0\u5F3A\u76D1\u6D4B\u3002",
            context=_scenario_context("worsening"),
        )
    )

    assert draft_text.startswith("\u3010\u75C5\u91CD\uFF08\u75C5\u5371\uFF09\u60A3\u8005\u62A4\u7406\u8BB0\u5F55\u5355\u3011")
    assert structured_fields["document_type"] == "critical_patient_nursing_record"
    assert structured_fields["template_name"] == "\u75C5\u91CD\uFF08\u75C5\u5371\uFF09\u60A3\u8005\u62A4\u7406\u8BB0\u5F55\u5355"
    assert "SpO2\uff1a93%" in draft_text
    assert "\u5FC3\u7387/\u8109\u640F\uff1A96\u6B21/\u5206" in draft_text
    assert "\u547C\u5438\uff1A24\u6B21/\u5206" in draft_text


def test_build_document_draft_filters_document_status_from_pending_tasks() -> None:
    settings.local_llm_enabled = False
    settings.local_only_mode = True

    context = _scenario_context("worsening")
    context["pending_tasks"] = [
        "\u6587\u4E66\u72B6\u6001\uFF1A\u8349\u7A3F\uFF0804-04 09:00:00\uFF09",
        "\u4F4E\u6C27\u98CE\u9669\u4E0A\u62A5\u533B\u751F",
        "\u7EE7\u7EED\u76D1\u6D4B\u547C\u5438",
    ]

    draft_text, structured_fields = asyncio.run(
        build_document_draft(
            document_type="\u75C5\u91CD\uFF08\u75C5\u5371\uFF09\u62A4\u7406\u8BB0\u5F55\u5355",
            spoken_text="\u60A3\u8005\u5B58\u5728\u4F4E\u6C27\u98CE\u9669\uFF0C\u9700\u52A0\u5F3A\u89C2\u5BDF\u3002",
            context=context,
        )
    )

    assert "\u6587\u4E66\u72B6\u6001\uFF1A" not in draft_text
    assert structured_fields["pending_tasks"] == ["\u4F4E\u6C27\u98CE\u9669\u4E0A\u62A5\u533B\u751F", "\u7EE7\u7EED\u76D1\u6D4B\u547C\u5438"]
