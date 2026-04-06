from __future__ import annotations

from typing import Any


SYSTEM_TEMPLATE_DEFINITIONS: list[dict[str, Any]] = [
    {
        "id": "tpl-system-nursing-note",
        "name": "护理记录单",
        "document_type": "nursing_note",
        "trigger_keywords": ["护理记录", "护理记录单", "特殊情况记录", "生命体征记录"],
        "source_refs": [
            "data/template_imports/various_nursing_records/各种护理记录单/护理记录单.docx",
        ],
        "template_text": (
            "【护理记录单】\n"
            "姓名：{{patient_name}}  性别：{{gender}}  年龄：{{age}}  科别：{{department_name}}\n"
            "床号：{{bed_no}}  病案号：{{mrn}}  住院号：{{inpatient_no}}\n"
            "日期：{{chart_date}}  时间：{{current_time}}  意识：{{consciousness}}\n"
            "T：{{temperature_value}}℃  P：{{pulse_value}}次/分  HR：{{heart_rate_value}}次/分  R：{{respiratory_rate}}次/分\n"
            "BP：{{blood_pressure}}mmHg  SpO2：{{spo2_value}}%  CVP：{{cvp_value}}cmH2O  血糖：{{blood_glucose_value}}mmol/L\n"
            "入量：{{intake_summary}}\n"
            "出量：{{output_summary}}\n"
            "特殊情况记录：{{special_notes}}\n"
            "护士签名：{{requested_by}}  上级签名：{{supervisor_sign}}\n"
        ),
    },
    {
        "id": "tpl-system-temperature-chart",
        "name": "体温单",
        "document_type": "temperature_chart",
        "trigger_keywords": ["体温单", "体温", "脉搏", "疼痛评分", "尿量", "总入量", "总出量"],
        "source_refs": [
            "data/template_imports/various_nursing_records/各种护理记录单/体温单1.docx",
        ],
        "template_text": (
            "【体温单】\n"
            "姓名：{{patient_name}}  科别：{{department_name}}  床号：{{bed_no}}  入院日期：{{admission_date}}  住院号：{{inpatient_no}}\n"
            "记录日期：{{chart_date}}  住院天数：{{hospital_day}}  术后天数：{{post_op_day}}  测量时间：{{current_time}}\n"
            "体温：{{temperature_value}}℃  脉搏：{{pulse_value}}次/分  呼吸：{{respiratory_rate}}次/分  疼痛强度：{{pain_score}}\n"
            "血压(上午)：{{blood_pressure_morning}}  血压(中午)：{{blood_pressure_noon}}  血压(下午)：{{blood_pressure_afternoon}}  血压(晚上)：{{blood_pressure_night}}\n"
            "身高：{{height}}  体重：{{weight}}  尿量：{{urine_volume}}ml  总入量：{{intake_total}}ml  总出量：{{output_total}}ml  大便次数：{{stool_count}}\n"
            "备注：{{spoken_text}}\n"
            "记录护士：{{requested_by}}\n"
        ),
    },
    {
        "id": "tpl-system-surgical-count",
        "name": "手术物品清单",
        "document_type": "surgical_count_record",
        "trigger_keywords": ["手术物品清单", "器械清点", "敷料清点", "术毕交接"],
        "source_refs": [
            "data/template_imports/various_nursing_records/各种护理记录单/手术物品清单.docx",
        ],
        "template_text": (
            "【手术物品清单】\n"
            "患者姓名：{{patient_name}}  性别：{{gender}}  年龄：{{age}}  手术日期：{{chart_date}}\n"
            "手术名称：{{operation_name}}\n"
            "术前准备：{{preop_preparation}}\n"
            "静脉通道：{{venous_access}}  导尿情况：{{catheter_status}}  药物过敏史：{{drug_allergy}}\n"
            "静脉穿刺部位：{{puncture_site}}  体位及固定方法：{{position_fixation}}\n"
            "电刀使用情况：{{electrotome_status}}  标本送病理情况：{{specimen_status}}\n"
            "特殊情况记录：{{special_record}}\n"
            "器械清点：{{instrument_count}}\n"
            "敷料清点：{{dressing_count}}\n"
            "特殊物品清点：{{special_item_count}}\n"
            "完整性核对结果：{{integrity_check}}\n"
            "术毕情况：{{operation_end_status}}\n"
            "出室血压：{{blood_pressure}}  出室脉搏：{{pulse_value}}\n"
            "引流管放置情况：{{drainage_status}}\n"
            "物品交接：{{handover_items}}\n"
            "护士签名：{{requested_by}}\n"
        ),
    },
    {
        "id": "tpl-system-critical-patient-record",
        "name": "病重（病危）患者护理记录单",
        "document_type": "critical_patient_nursing_record",
        "trigger_keywords": ["病重护理记录", "病危护理记录", "危重患者护理记录"],
        "source_refs": ["system://critical_patient_nursing_record"],
        "template_text": (
            "【病重（病危）患者护理记录单】\n"
            "科别：{{department_name}}  姓名：{{patient_name}}  床号：{{bed_no}}  病案号：{{mrn}}\n"
            "记录时间：{{current_time}}\n"
            "主要诊断：{{diagnoses}}\n"
            "体温：{{temperature_value}}℃  心率/脉搏：{{pulse_value}}  呼吸：{{respiratory_rate}}  SpO2：{{spo2_value}}  血压：{{blood_pressure}}\n"
            "入量：{{intake_total}}  出量：{{output_total}}\n"
            "病情观察：{{observation_summary}}\n"
            "风险等级：{{risk_level}}  重点风险：{{risk_tags}}\n"
            "护理措施与效果：{{spoken_text}}\n"
            "下一班观察重点：{{pending_tasks}}\n"
            "护士签名：{{requested_by}}\n"
        ),
    },
    {
        "id": "tpl-system-transfusion-record",
        "name": "输血记录单",
        "document_type": "transfusion_nursing_record",
        "trigger_keywords": ["输血记录单", "输血前查对", "输血中巡察", "输血反应"],
        "source_refs": [
            "data/template_imports/various_nursing_records/各种护理记录单/输血记录单.docx",
        ],
        "template_text": (
            "【输血记录单】\n"
            "患者姓名：{{patient_name}}  性别：{{gender}}  年龄：{{age}}  民族：{{ethnicity}}\n"
            "科别：{{department_name}}  病室：{{ward_name}}  床号：{{bed_no}}  住院号：{{inpatient_no}}\n"
            "输血反应史：{{transfusion_reaction_history}}\n"
            "输血前查对内容：{{precheck_summary}}\n"
            "查对人签字：{{requested_by}}  复核人签字：{{double_checker}}\n"
            "输血前体温：{{temperature_before}}  输血前血压：{{blood_pressure_before}}\n"
            "输血开始时间：{{transfusion_start_time}}\n"
            "输血中巡察记录：{{spoken_text}}\n"
            "输血结束时间：{{transfusion_end_time}}\n"
            "结束时体温：{{temperature_after}}  结束时血压：{{blood_pressure_after}}\n"
            "实际输入量：{{actual_transfusion_volume}}\n"
            "输血反应及处理：{{transfusion_reaction}}\n"
        ),
    },
    {
        "id": "tpl-system-transfusion-process-record",
        "name": "临床输血过程记录单",
        "document_type": "transfusion_process_record",
        "trigger_keywords": ["输血过程记录", "临床输血过程", "输血流速", "输血不良反应"],
        "source_refs": [
            "data/template_imports/various_nursing_records/各种护理记录单/输血过程记录单.docx",
        ],
        "template_text": (
            "【临床输血过程记录单】\n"
            "记录人：{{requested_by}}  复核人：{{double_checker}}  记录时间：{{current_time}}\n"
            "受血者姓名：{{patient_name}}  病案号：{{mrn}}  性别：{{gender}}  年龄：{{age}}\n"
            "血型：{{blood_type}}  Rh血型：{{rh_type}}  科别：{{department_name}}  病区：{{ward_name}}  床号：{{bed_no}}\n"
            "输血性质：{{transfusion_priority}}  供血者条码号：{{donor_barcode}}\n"
            "血液成分：{{blood_component}}  血量：{{blood_volume}}ml\n"
            "开始时间：{{transfusion_start_time}}  初始流速：{{first_stage_rate}}  初始不良反应：{{first_stage_reaction}}\n"
            "中段流速：{{second_stage_rate}}  中段不良反应：{{second_stage_reaction}}\n"
            "结束时间：{{transfusion_end_time}}  末段不良反应：{{transfusion_reaction}}\n"
            "备注：{{spoken_text}}\n"
        ),
    },
    {
        "id": "tpl-system-glucose-record",
        "name": "血糖记录单",
        "document_type": "glucose_record",
        "trigger_keywords": ["血糖记录单", "随机血糖", "早餐血糖", "午餐血糖", "晚餐血糖"],
        "source_refs": [
            "data/template_imports/various_nursing_records/各种护理记录单/血糖记录单.docx",
        ],
        "template_text": (
            "【血糖记录单】\n"
            "姓名：{{patient_name}}  性别：{{gender}}  年龄：{{age}}  科别：{{department_name}}  床号：{{bed_no}}  住院号：{{inpatient_no}}\n"
            "日期：{{chart_date}}\n"
            "早餐前：{{breakfast_before_glucose}}  早餐后：{{breakfast_after_glucose}}\n"
            "午餐前：{{lunch_before_glucose}}  午餐后：{{lunch_after_glucose}}\n"
            "晚餐前：{{dinner_before_glucose}}  晚餐后：{{dinner_after_glucose}}\n"
            "睡前血糖：{{bedtime_glucose}}\n"
            "随机血糖时间：{{current_time}}  随机血糖值：{{glucose_value}}\n"
            "补充说明：{{spoken_text}}\n"
            "记录人：{{requested_by}}\n"
        ),
    },
    {
        "id": "tpl-system-handover-report",
        "name": "大交班报告",
        "document_type": "nursing_handover_report",
        "trigger_keywords": ["大交班", "日夜交班", "交班报告", "交接班摘要"],
        "source_refs": [
            "data/template_imports/various_nursing_records/各种护理记录单/日夜交班.docx",
        ],
        "template_text": (
            "【大交班报告】\n"
            "科室：{{department_name}}  交班日期：{{shift_date}}  班次：{{shift_type}}\n"
            "总人数：{{ward_total}}  出院：{{discharge_count}}  转出：{{transfer_out_count}}  入院：{{admission_count}}  转入：{{transfer_in_count}}\n"
            "手术/介入：{{operation_count}}  明日手术/介入：{{tomorrow_operation_count}}  病重：{{serious_count}}  病危：{{critical_count}}  死亡：{{death_count}}\n"
            "特殊疾病护理：{{special_disease_care}}\n"
            "其他：{{other_summary}}\n"
            "传染病报卡：{{infectious_report}}\n"
            "重点患者：床号 {{bed_no}} / 姓名 {{patient_name}} / 年龄 {{age}} / 性别 {{gender}} / 住院号 {{inpatient_no}}\n"
            "诊断：{{diagnoses}}\n"
            "特殊病情：{{observation_summary}}\n"
            "24小时病情及处理：{{spoken_text}}\n"
            "特殊事件：{{special_events}}\n"
            "接班人签名：{{receiver_sign}}  交班人签名：{{requested_by}}\n"
        ),
    },
    {
        "id": "tpl-system-stroke-tcm-evaluation",
        "name": "中风（脑梗死急性期）中医护理效果评价表",
        "document_type": "stroke_tcm_nursing_effect_evaluation",
        "trigger_keywords": ["中风护理方案", "脑梗死急性期", "中医护理效果评价", "辨证施护"],
        "source_refs": [
            "data/template_imports/various_nursing_records/各种护理记录单/中风（脑梗死急性期）中医护理方案(第二次修订).doc",
        ],
        "template_text": (
            "【中风（脑梗死急性期）中医护理效果评价表】\n"
            "医院：{{hospital_name}}  患者姓名：{{patient_name}}  性别：{{gender}}  年龄：{{age}}  ID：{{patient_identifier}}\n"
            "文化程度：{{education_level}}  电话：{{phone}}  入院日期：{{admission_date}}\n"
            "阳类证：{{yang_pattern}}\n"
            "阴类证：{{yin_pattern}}\n"
            "脱证：{{collapse_pattern}}\n"
            "主要症状：{{symptom_name}}\n"
            "主要辨证施护方法：{{nursing_method}}\n"
            "中医护理技术：{{tcm_technique}}\n"
            "实施前：{{severity_before_date}} / {{severity_before_score}}\n"
            "实施后：{{severity_after_date}} / {{severity_after_score}}\n"
            "效果评价：{{effect_evaluation}}\n"
            "补充说明：{{spoken_text}}\n"
            "责任护士：{{requested_by}}\n"
        ),
    },
]


def system_templates() -> list[dict[str, Any]]:
    return [dict(item) for item in SYSTEM_TEMPLATE_DEFINITIONS]
