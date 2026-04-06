import { decodeEscapedText } from "./text";

const DOCUMENT_TYPE_LABELS: Record<string, string> = {
  nursing_note: "护理记录单",
  general_nursing_record: "护理记录单",
  transfusion_nursing_record: "输血记录单",
  blood_transfusion_nursing_record: "输血记录单",
  transfusion_process_record: "临床输血过程记录单",
  temperature_chart: "体温单",
  critical_patient_nursing_record: "病重（病危）患者护理记录单",
  critical_nursing_record: "病重（病危）患者护理记录单",
  surgical_count_record: "手术物品清单",
  surgical_item_count_record: "手术物品清单",
  blood_glucose_record: "血糖记录单",
  glucose_record: "血糖记录单",
  nursing_handover_report: "大交班报告",
  handover_report: "大交班报告",
  nursing_shift_report: "大交班报告",
  stroke_tcm_nursing_effect_evaluation: "中风（脑梗死急性期）中医护理效果评价表",
  progress_note: "病程记录",
};

const STATUS_LABELS: Record<string, string> = {
  draft: "草稿",
  reviewed: "已审核",
  submitted: "已归档",
  archived: "已归档",
  completed: "已完成",
  done: "已完成",
  success: "已完成",
  pending: "待处理",
  queued: "排队中",
  running: "执行中",
  waiting: "等待中",
  waiting_approval: "待批准",
  skipped: "已跳过",
  failed: "失败",
  cancelled: "已取消",
};

const WORKFLOW_TYPE_LABELS: Record<string, string> = {
  voice_inquiry: "病例问答",
  single_model_chat: "单模型对话",
  handover_generate: "交接班生成",
  compare_handover: "多床交班比较",
  document_generation: "文书生成",
  recommendation_request: "护理建议",
  autonomous_care: "持续跟进",
  autonomous_loop: "持续跟进",
  system_coordination: "系统协同",
  tcm_consult: "中医护理问诊",
};

const SOURCE_TYPE_LABELS: Record<string, string> = {
  ai: "智能生成",
  manual: "人工填写",
  template: "系统模板",
  import: "导入模板",
  imported: "导入模板",
  sync: "同步导入",
  system: "标准模板",
};

const MODE_LABELS: Record<string, string> = {
  single_model: "单模型",
  agent_cluster: "智能协作",
};

const EXECUTION_PROFILE_LABELS: Record<string, string> = {
  observe: "快速观察",
  escalate: "上报沟通",
  document: "文书处理",
  full_loop: "持续跟进",
  single_model: "单模型直答",
  agent: "标准协作",
};

const ROLE_LABELS: Record<string, string> = {
  nurse: "护士",
  doctor: "医生",
  charge_nurse: "护士长",
  admin: "管理员",
};

const ENGINE_LABELS: Record<string, string> = {
  state_machine: "标准编排",
  langgraph: "深度编排",
};

const MODEL_LABELS: Record<string, string> = {
  minicpm3_4b_local: "本地通用模型",
  "minicpm3-4b-q4_k_m": "本地通用模型",
  qwen2_5_3b_local: "本地轻量模型",
  "qwen2.5-3b-instruct-q4_k_m": "本地轻量模型",
  tcm_local_model: "本地中医模型",
  shennong_tcm_local: "本地中医模型",
  "shennong-tcm-llm-8b": "本地中医模型",
  "deepseek-r1-distill-qwen-7b": "本地推理模型",
  "qwen3-8b": "本地规划模型",
};

const CLUSTER_LABELS: Record<string, string> = {
  nursing_default_cluster: "临床协同",
  tcm_nursing_cluster: "中医护理协同",
};

function hasChinese(value: string) {
  return /[\u4e00-\u9fff]/.test(value);
}

function normalize(value?: string | null) {
  return String(value || "").trim().toLowerCase();
}

function fallbackLabel(label: string, fallback: string) {
  const text = decodeEscapedText(label).trim();
  if (!text) {
    return fallback;
  }
  return hasChinese(text) ? text : fallback;
}

export function getDocumentTypeLabel(value?: string | null) {
  const key = normalize(value);
  return DOCUMENT_TYPE_LABELS[key] || fallbackLabel(String(value || ""), "护理文书");
}

export function getStatusLabel(value?: string | null) {
  const key = normalize(value);
  return STATUS_LABELS[key] || fallbackLabel(String(value || ""), "处理中");
}

export function getWorkflowTypeLabel(value?: string | null) {
  const key = normalize(value);
  return WORKFLOW_TYPE_LABELS[key] || fallbackLabel(String(value || ""), "护理任务");
}

export function getSourceTypeLabel(value?: string | null) {
  const key = normalize(value);
  return SOURCE_TYPE_LABELS[key] || fallbackLabel(String(value || ""), "系统来源");
}

export function getModeLabel(value?: string | null) {
  const key = normalize(value);
  return MODE_LABELS[key] || fallbackLabel(String(value || ""), "智能协作");
}

export function getExecutionProfileLabel(value?: string | null) {
  const key = normalize(value);
  return EXECUTION_PROFILE_LABELS[key] || fallbackLabel(String(value || ""), "标准处理");
}

export function getRoleLabel(value?: string | null) {
  const key = normalize(value);
  return ROLE_LABELS[key] || fallbackLabel(String(value || ""), "护理人员");
}

export function getEngineLabel(value?: string | null) {
  const key = normalize(value);
  return ENGINE_LABELS[key] || fallbackLabel(String(value || ""), "标准编排");
}

export function getModelLabel(value?: string | null) {
  const key = normalize(value);
  return MODEL_LABELS[key] || fallbackLabel(String(value || ""), "本地模型");
}

export function getClusterLabel(value?: string | null) {
  const key = normalize(value);
  return CLUSTER_LABELS[key] || fallbackLabel(String(value || ""), "临床协同");
}

export function getDepartmentLabel(value?: string | null) {
  const text = String(value || "").trim();
  if (!text) {
    return "当前病区";
  }
  if (hasChinese(text)) {
    return text;
  }
  const matched = text.match(/(\d+)/);
  if (matched?.[1]) {
    return `${matched[1]}号病区`;
  }
  return "当前病区";
}
