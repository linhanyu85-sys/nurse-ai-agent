import type { DocumentTemplate, StandardFormBundle } from "../types";

const ZIP_ROOT = "D:\\Desktop\\各种护理记录单.zip\\各种护理记录单";
const NOW_ISO = new Date("2026-04-05T18:00:00+08:00").toISOString();

type SheetField = StandardFormBundle["sheet_columns"][number];

function normalize(value?: string | null) {
  return String(value || "").trim().toLowerCase();
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value));
}

function field(
  key: string,
  label: string,
  section: string,
  required = false,
  inputType?: SheetField["input_type"]
): SheetField {
  return {
    key,
    label,
    section,
    required,
    input_type: inputType,
  };
}

function buildSections(fields: SheetField[]) {
  return Array.from(new Set(fields.map((item) => item.section))).map((title) => ({ title }));
}

function buildForm(
  documentType: string,
  formId: string,
  name: string,
  fileName: string,
  description: string,
  fields: SheetField[]
): StandardFormBundle {
  return {
    document_type: documentType,
    form_id: formId,
    name,
    standard_family: "导入护理文书模板",
    description,
    schema_version: "2026.04.local",
    source_refs: [`${ZIP_ROOT}\\${fileName}`],
    sections: buildSections(fields),
    field_count: fields.length,
    sheet_columns: fields,
    questionnaire: {},
  };
}

function buildImportedTemplate(
  id: string,
  name: string,
  documentType: string,
  fileName: string,
  triggerKeywords: string[],
  templateText: string
): DocumentTemplate {
  return {
    id,
    name,
    source_type: "import",
    document_type: documentType,
    trigger_keywords: triggerKeywords,
    source_refs: [`${ZIP_ROOT}\\${fileName}`],
    template_text: templateText,
    created_by: "local-import",
    created_at: NOW_ISO,
    updated_at: NOW_ISO,
  };
}

const NURSING_NOTE_FIELDS: SheetField[] = [
  field("patient_name", "姓名", "患者信息", true),
  field("gender", "性别", "患者信息"),
  field("age", "年龄", "患者信息"),
  field("department_name", "科别", "患者信息"),
  field("bed_no", "床号", "患者信息", true),
  field("inpatient_no", "住院号", "患者信息"),
  field("chart_date", "日期", "记录信息", true),
  field("current_time", "时间", "记录信息", true),
  field("consciousness", "意识", "生命体征"),
  field("temperature_value", "T ℃", "生命体征"),
  field("pulse_value", "P 次/min", "生命体征"),
  field("heart_rate_value", "HR 次/min", "生命体征"),
  field("respiratory_rate", "R 次/min", "生命体征"),
  field("blood_pressure", "BP mmHg", "生命体征"),
  field("spo2_value", "SpO2 %", "生命体征"),
  field("cvp_value", "CVP cmH2O", "生命体征"),
  field("blood_glucose_value", "血糖 mmol/L", "生命体征"),
  field("intake_total", "入量 ml", "出入量"),
  field("output_total", "出量 ml", "出入量"),
  field("spoken_text", "特殊情况记录", "护理记录", true, "textarea"),
  field("nurse_sign", "护士签名", "签名信息"),
  field("supervisor_sign", "上级签名", "签名信息"),
];

const TEMPERATURE_CHART_FIELDS: SheetField[] = [
  field("patient_name", "姓名", "患者信息", true),
  field("department_name", "科别", "患者信息"),
  field("bed_no", "床号", "患者信息", true),
  field("admission_date", "入院日期", "患者信息"),
  field("inpatient_no", "住院号", "患者信息"),
  field("chart_date", "日期", "体温单信息", true),
  field("temperature_value", "体温 ℃", "体征记录"),
  field("pulse_value", "脉搏 次/分", "体征记录"),
  field("respiratory_rate", "呼吸 次/分", "体征记录"),
  field("blood_pressure", "血压 mmHg", "体征记录"),
  field("pain_score", "疼痛强度", "体征记录"),
  field("stool_times", "大便次数", "出入量"),
  field("urine_output", "尿量 ml", "出入量"),
  field("intake_total", "总入量 ml", "出入量"),
  field("output_total", "总出量 ml", "出入量"),
  field("special_notes", "补充记录", "备注", false, "textarea"),
];

const SURGICAL_COUNT_FIELDS: SheetField[] = [
  field("patient_name", "患者姓名", "患者信息", true),
  field("gender", "性别", "患者信息"),
  field("age", "年龄", "患者信息"),
  field("chart_date", "手术日期", "手术信息", true),
  field("operation_name", "手术名称", "手术信息", true),
  field("bed_no", "床号", "患者信息"),
  field("instrument_count", "器械清点", "清点结果", true, "textarea"),
  field("dressing_count", "敷料清点", "清点结果", true, "textarea"),
  field("special_item_count", "特殊物品", "清点结果"),
  field("specimen_status", "标本情况", "术中处理"),
  field("drain_status", "引流管放置", "术后交接"),
  field("special_notes", "特殊情况记录", "术后交接", false, "textarea"),
  field("nurse_sign", "护士签名", "签名信息"),
];

const TRANSFUSION_RECORD_FIELDS: SheetField[] = [
  field("patient_name", "患者姓名", "患者信息", true),
  field("gender", "性别", "患者信息"),
  field("age", "年龄", "患者信息"),
  field("department_name", "科别", "患者信息"),
  field("ward_name", "病室", "患者信息"),
  field("bed_no", "床号", "患者信息", true),
  field("inpatient_no", "住院号", "患者信息"),
  field("blood_type", "血型", "输血前评估"),
  field("transfusion_reaction_history", "输血反应史", "输血前评估"),
  field("temperature_value", "输血前体温 ℃", "生命体征"),
  field("blood_pressure", "输血前血压 mmHg", "生命体征"),
  field("transfusion_start_time", "输血开始时间", "输血过程", true),
  field("transfusion_end_time", "输血结束时间", "输血过程"),
  field("transfusion_reaction", "输血反应", "输血过程", false, "textarea"),
  field("spoken_text", "输血中巡视记录", "输血过程", true, "textarea"),
  field("intake_total", "实际输入量", "输血结果"),
  field("nurse_sign", "查对人签字", "签名信息"),
  field("reviewer_sign", "复核人签字", "签名信息"),
];

const TRANSFUSION_PROCESS_FIELDS: SheetField[] = [
  field("patient_name", "受血者姓名", "患者信息", true),
  field("mrn", "病案号", "患者信息", true),
  field("gender", "性别", "患者信息"),
  field("age", "年龄", "患者信息"),
  field("blood_type", "血型", "患者信息"),
  field("department_name", "科别", "患者信息"),
  field("ward_name", "病区", "患者信息"),
  field("bed_no", "床号", "患者信息", true),
  field("current_time", "记录时间", "记录信息", true),
  field("transfusion_component", "血液成分", "输血信息"),
  field("intake_total", "血量 ml", "输血信息"),
  field("transfusion_start_time", "开始时间", "输血过程", true),
  field("transfusion_rate", "输血速度", "输血过程"),
  field("transfusion_end_time", "结束时间", "输血过程"),
  field("transfusion_reaction", "输血不良反应", "输血过程", false, "textarea"),
  field("special_notes", "备注", "输血过程", false, "textarea"),
  field("recorder_sign", "记录人", "签名信息"),
  field("reviewer_sign", "复核人", "签名信息"),
];

const GLUCOSE_RECORD_FIELDS: SheetField[] = [
  field("patient_name", "姓名", "患者信息", true),
  field("gender", "性别", "患者信息"),
  field("age", "年龄", "患者信息"),
  field("department_name", "科别", "患者信息"),
  field("bed_no", "床号", "患者信息", true),
  field("inpatient_no", "住院号", "患者信息"),
  field("chart_date", "日期", "记录信息", true),
  field("breakfast_glucose", "早餐 mmol/L", "血糖记录"),
  field("lunch_glucose", "午餐 mmol/L", "血糖记录"),
  field("dinner_glucose", "晚餐 mmol/L", "血糖记录"),
  field("bedtime_glucose", "睡前 mmol/L", "血糖记录"),
  field("current_time", "随机血糖时间", "随机血糖"),
  field("glucose_value", "随机血糖值", "随机血糖"),
  field("special_notes", "补充说明", "备注", false, "textarea"),
];

const HANDOVER_FIELDS: SheetField[] = [
  field("department_name", "科室", "交班信息", true),
  field("shift_date", "交班日期", "交班信息", true),
  field("shift_type", "班次", "交班信息", true),
  field("patient_name", "姓名", "患者条目", true),
  field("age", "年龄", "患者条目"),
  field("gender", "性别", "患者条目"),
  field("bed_no", "床号", "患者条目", true),
  field("inpatient_no", "住院号", "患者条目"),
  field("diagnoses", "诊断", "患者条目"),
  field("observation_summary", "特殊病情", "患者条目", true, "textarea"),
  field("pending_tasks", "24 小时交接重点", "患者条目", true, "textarea"),
  field("special_notes", "特殊事件", "交班信息", false, "textarea"),
  field("receiver_sign", "接班人签名", "签名信息"),
  field("handover_sign", "交班人签名", "签名信息"),
];

const STROKE_TCM_FIELDS: SheetField[] = [
  field("patient_name", "患者姓名", "患者信息", true),
  field("bed_no", "床号", "患者信息"),
  field("diagnoses", "西医诊断", "辨证信息", true),
  field("tcm_syndrome", "中医证型", "辨证信息", true),
  field("observation_summary", "症状观察", "评估内容", true, "textarea"),
  field("nursing_measure", "中医护理措施", "评估内容", true, "textarea"),
  field("effect_evaluation", "护理效果评价", "评估内容", true, "textarea"),
  field("pending_tasks", "后续护理重点", "评估内容", false, "textarea"),
  field("current_time", "记录时间", "记录信息", true),
  field("nurse_sign", "责任护士", "签名信息"),
];

export const LOCAL_STANDARD_FORMS: StandardFormBundle[] = [
  buildForm(
    "nursing_note",
    "local-form-nursing-note",
    "护理记录单",
    "护理记录单.docx",
    "按护理记录单标准栏目补录患者标识、生命体征、出入量与特殊情况记录。",
    NURSING_NOTE_FIELDS
  ),
  buildForm(
    "temperature_chart",
    "local-form-temperature-chart",
    "体温单",
    "体温单1.docx",
    "用于按班次补录体温、脉搏、呼吸、血压、疼痛与出入量。",
    TEMPERATURE_CHART_FIELDS
  ),
  buildForm(
    "surgical_count_record",
    "local-form-surgical-count-record",
    "手术物品清单",
    "手术物品清单.docx",
    "用于术前术中术后器械、敷料与特殊物品清点记录。",
    SURGICAL_COUNT_FIELDS
  ),
  buildForm(
    "transfusion_nursing_record",
    "local-form-transfusion-record",
    "输血记录单",
    "输血记录单.docx",
    "用于输血前评估、输血过程巡视与输血反应记录。",
    TRANSFUSION_RECORD_FIELDS
  ),
  buildForm(
    "transfusion_process_record",
    "local-form-transfusion-process-record",
    "临床输血过程记录单",
    "输血过程记录单.docx",
    "用于逐时记录输血速度、输血反应与处理经过。",
    TRANSFUSION_PROCESS_FIELDS
  ),
  buildForm(
    "glucose_record",
    "local-form-glucose-record",
    "血糖记录单",
    "血糖记录单.docx",
    "用于记录三餐前后、睡前与随机血糖结果。",
    GLUCOSE_RECORD_FIELDS
  ),
  buildForm(
    "nursing_handover_report",
    "local-form-handover-report",
    "大交班报告",
    "日夜交班.docx",
    "用于交班日期、患者重点病情与 24 小时交接事项留痕。",
    HANDOVER_FIELDS
  ),
  buildForm(
    "stroke_tcm_nursing_effect_evaluation",
    "local-form-stroke-tcm-plan",
    "中风（脑梗死急性期）中医护理方案",
    "中风（脑梗死急性期）中医护理方案(第二次修订).doc",
    "用于中风急性期中医证型、护理措施与效果评价记录。",
    STROKE_TCM_FIELDS
  ),
];

export const LOCAL_IMPORTED_TEMPLATES: DocumentTemplate[] = [
  buildImportedTemplate(
    "tpl-local-import-nursing-note",
    "护理记录单",
    "nursing_note",
    "护理记录单.docx",
    ["护理记录", "护理记录单", "护理文书"],
    [
      "【护理记录单】",
      "姓名：{{patient_name}}",
      "性别：{{gender}}",
      "年龄：{{age}}",
      "科别：{{department_name}}",
      "床号：{{bed_no}}",
      "住院号：{{inpatient_no}}",
      "日期：{{chart_date}}",
      "时间：{{current_time}}",
      "意识：{{consciousness}}",
      "T：{{temperature_value}}",
      "P：{{pulse_value}}",
      "HR：{{heart_rate_value}}",
      "R：{{respiratory_rate}}",
      "BP：{{blood_pressure}}",
      "SpO2：{{spo2_value}}",
      "CVP：{{cvp_value}}",
      "血糖：{{blood_glucose_value}}",
      "入量：{{intake_total}}",
      "出量：{{output_total}}",
      "特殊情况记录：{{spoken_text}}",
      "护士签名：",
      "上级签名：",
    ].join("\n")
  ),
  buildImportedTemplate(
    "tpl-local-import-temperature-chart",
    "体温单",
    "temperature_chart",
    "体温单1.docx",
    ["体温单", "生命体征", "护理体征"],
    [
      "【体温单】",
      "姓名：{{patient_name}}",
      "科别：{{department_name}}",
      "床号：{{bed_no}}",
      "入院日期：{{admission_date}}",
      "住院号：{{inpatient_no}}",
      "日期：{{chart_date}}",
      "体温：{{temperature_value}}",
      "脉搏：{{pulse_value}}",
      "呼吸：{{respiratory_rate}}",
      "血压：{{blood_pressure}}",
      "疼痛强度：{{pain_score}}",
      "大便次数：{{stool_times}}",
      "尿量：{{urine_output}}",
      "总入量：{{intake_total}}",
      "总出量：{{output_total}}",
      "补充：{{special_notes}}",
    ].join("\n")
  ),
  buildImportedTemplate(
    "tpl-local-import-surgical-count",
    "手术物品清单",
    "surgical_count_record",
    "手术物品清单.docx",
    ["手术物品清单", "器械清点", "敷料清点"],
    [
      "【手术物品清单】",
      "患者姓名：{{patient_name}}",
      "性别：{{gender}}",
      "年龄：{{age}}",
      "手术日期：{{chart_date}}",
      "手术名称：{{operation_name}}",
      "床号：{{bed_no}}",
      "器械清点：{{instrument_count}}",
      "敷料清点：{{dressing_count}}",
      "特殊物品：{{special_item_count}}",
      "标本情况：{{specimen_status}}",
      "引流管放置：{{drain_status}}",
      "特殊情况记录：{{special_notes}}",
      "护士签名：{{nurse_sign}}",
    ].join("\n")
  ),
  buildImportedTemplate(
    "tpl-local-import-transfusion-record",
    "输血记录单",
    "transfusion_nursing_record",
    "输血记录单.docx",
    ["输血记录单", "输血护理", "输血巡视"],
    [
      "【输血记录单】",
      "患者姓名：{{patient_name}}",
      "性别：{{gender}}",
      "年龄：{{age}}",
      "科别：{{department_name}}",
      "病室：{{ward_name}}",
      "床号：{{bed_no}}",
      "住院号：{{inpatient_no}}",
      "血型：{{blood_type}}",
      "输血反应史：{{transfusion_reaction_history}}",
      "输血前体温：{{temperature_value}}",
      "输血前血压：{{blood_pressure}}",
      "输血开始时间：{{transfusion_start_time}}",
      "输血结束时间：{{transfusion_end_time}}",
      "输血中巡视记录：{{spoken_text}}",
      "输血反应：{{transfusion_reaction}}",
      "实际输入量：{{intake_total}}",
      "查对人签字：{{nurse_sign}}",
      "复核人签字：{{reviewer_sign}}",
    ].join("\n")
  ),
  buildImportedTemplate(
    "tpl-local-import-transfusion-process",
    "输血过程记录单",
    "transfusion_process_record",
    "输血过程记录单.docx",
    ["输血过程记录单", "临床输血过程", "输血反应"],
    [
      "【临床输血过程记录单】",
      "记录人：{{recorder_sign}}",
      "复核人：{{reviewer_sign}}",
      "记录时间：{{current_time}}",
      "受血者姓名：{{patient_name}}",
      "病案号：{{mrn}}",
      "性别：{{gender}}",
      "年龄：{{age}}",
      "血型：{{blood_type}}",
      "科别：{{department_name}}",
      "病区：{{ward_name}}",
      "床号：{{bed_no}}",
      "血液成分：{{transfusion_component}}",
      "血量：{{intake_total}} ml",
      "开始时间：{{transfusion_start_time}}",
      "输血速度：{{transfusion_rate}}",
      "结束时间：{{transfusion_end_time}}",
      "输血不良反应：{{transfusion_reaction}}",
      "备注：{{special_notes}}",
    ].join("\n")
  ),
  buildImportedTemplate(
    "tpl-local-import-glucose-record",
    "血糖记录单",
    "glucose_record",
    "血糖记录单.docx",
    ["血糖记录单", "血糖", "随机血糖"],
    [
      "【血糖记录单】",
      "姓名：{{patient_name}}",
      "性别：{{gender}}",
      "年龄：{{age}}",
      "科别：{{department_name}}",
      "床号：{{bed_no}}",
      "住院号：{{inpatient_no}}",
      "日期：{{chart_date}}",
      "早餐：{{breakfast_glucose}}",
      "午餐：{{lunch_glucose}}",
      "晚餐：{{dinner_glucose}}",
      "睡前：{{bedtime_glucose}}",
      "随机血糖时间：{{current_time}}",
      "随机血糖值：{{glucose_value}}",
      "补充：{{special_notes}}",
    ].join("\n")
  ),
  buildImportedTemplate(
    "tpl-local-import-handover-report",
    "大交班报告",
    "nursing_handover_report",
    "日夜交班.docx",
    ["大交班报告", "交班", "交接班"],
    [
      "【大交班报告】",
      "科室：{{department_name}}",
      "交班日期：{{shift_date}}",
      "班次：{{shift_type}}",
      "床号：{{bed_no}}",
      "姓名：{{patient_name}}",
      "年龄：{{age}}",
      "性别：{{gender}}",
      "住院号：{{inpatient_no}}",
      "诊断：{{diagnoses}}",
      "特殊病情：{{observation_summary}}",
      "24 小时交接重点：{{pending_tasks}}",
      "特殊事件：{{special_notes}}",
      "接班人签名：{{receiver_sign}}",
      "交班人签名：{{handover_sign}}",
    ].join("\n")
  ),
  buildImportedTemplate(
    "tpl-local-import-stroke-tcm",
    "中风（脑梗死急性期）中医护理方案",
    "stroke_tcm_nursing_effect_evaluation",
    "中风（脑梗死急性期）中医护理方案(第二次修订).doc",
    ["中风中医护理", "脑梗死急性期", "中医护理方案"],
    [
      "【中风（脑梗死急性期）中医护理方案】",
      "患者姓名：{{patient_name}}",
      "床号：{{bed_no}}",
      "西医诊断：{{diagnoses}}",
      "中医证型：{{tcm_syndrome}}",
      "症状观察：{{observation_summary}}",
      "中医护理措施：{{nursing_measure}}",
      "护理效果评价：{{effect_evaluation}}",
      "后续护理重点：{{pending_tasks}}",
      "记录时间：{{current_time}}",
      "责任护士：{{nurse_sign}}",
    ].join("\n")
  ),
];

const STANDARD_FORM_LOOKUP = new Map<string, StandardFormBundle>();

function registerForm(key: string, form: StandardFormBundle) {
  const normalized = normalize(key);
  if (!normalized || STANDARD_FORM_LOOKUP.has(normalized)) {
    return;
  }
  STANDARD_FORM_LOOKUP.set(normalized, form);
}

LOCAL_STANDARD_FORMS.forEach((form) => {
  registerForm(form.document_type, form);
  registerForm(form.form_id, form);
  registerForm(form.name, form);
});

registerForm("护理记录单", LOCAL_STANDARD_FORMS[0]);
registerForm("体温单", LOCAL_STANDARD_FORMS[1]);
registerForm("手术物品清单", LOCAL_STANDARD_FORMS[2]);
registerForm("输血记录单", LOCAL_STANDARD_FORMS[3]);
registerForm("输血过程记录单", LOCAL_STANDARD_FORMS[4]);
registerForm("血糖记录单", LOCAL_STANDARD_FORMS[5]);
registerForm("大交班报告", LOCAL_STANDARD_FORMS[6]);
registerForm("日夜交班", LOCAL_STANDARD_FORMS[6]);
registerForm("中风（脑梗死急性期）中医护理方案", LOCAL_STANDARD_FORMS[7]);

export function getLocalDocumentTemplates() {
  return clone(LOCAL_IMPORTED_TEMPLATES);
}

export function mergeDocumentTemplatesWithLocal(templates: DocumentTemplate[]) {
  const merged = getLocalDocumentTemplates();
  const seen = new Set(
    merged.map((item) => `${normalize(item.document_type || "")}:${normalize(item.name)}`)
  );

  (templates || []).forEach((item) => {
    const key = `${normalize(item.document_type || "")}:${normalize(item.name)}`;
    if (seen.has(key)) {
      return;
    }
    seen.add(key);
    merged.push(item);
  });

  return merged;
}

export function getLocalStandardForms() {
  return clone(LOCAL_STANDARD_FORMS);
}

export function mergeStandardFormsWithLocal(forms: StandardFormBundle[]) {
  const merged = getLocalStandardForms();
  const seen = new Set(merged.map((item) => normalize(item.document_type)));

  (forms || []).forEach((item) => {
    const key = normalize(item.document_type);
    if (seen.has(key)) {
      return;
    }
    seen.add(key);
    merged.push(item);
  });

  return merged;
}

export function getLocalStandardForm(key: string) {
  const matched = STANDARD_FORM_LOOKUP.get(normalize(key));
  return matched ? clone(matched) : null;
}
