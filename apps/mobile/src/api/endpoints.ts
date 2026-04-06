import { asrBaseURL, httpClient, isMockMode } from "./client";
import { mockApi } from "./mock";
import {
  getLocalStandardForm,
  getLocalStandardForms,
  mergeDocumentTemplatesWithLocal,
  mergeStandardFormsWithLocal,
} from "../constants/localDocumentLibrary";
import type {
  AssistantDigest,
  AIChatMode,
  AIExecutionProfile,
  AIChatResponse,
  AIModelsCatalog,
  AIRuntimeStatus,
  AgentRunRecord,
  AgentQueueTask,
  BedOverview,
  ClinicalOrder,
  CollabAccount,
  CollabContactList,
  CollaborationThreadDetail,
  CollaborationThreadHistoryItem,
  ConversationHistoryItem,
  DirectSession,
  DirectSessionDetail,
  DocumentDraft,
  StandardFormBundle,
  DocumentTemplate,
  HandoverResult,
  MultimodalAnalysisResult,
  OrderListOut,
  PatientScopePreview,
  Patient,
  PatientContext,
  RecommendationResult,
} from "../types";

function extractDetail(detail: unknown): string {
  if (typeof detail === "string") {
    return detail;
  }
  if (detail && typeof detail === "object") {
    const nested = (detail as any).detail;
    if (typeof nested === "string") {
      return nested;
    }
  }
  return "";
}

export function getApiErrorMessage(error: unknown, fallback = "服务暂时不可用，请稍后重试"): string {
  const status = Number((error as any)?.response?.status || 0);
  const detail = extractDetail((error as any)?.response?.data);

  if (status === 401 || detail === "invalid_credentials") {
    return "账号或密码不正确，请重新输入。";
  }
  if (status === 409 || detail === "username_exists") {
    return "该用户名已存在，请更换后再试。";
  }
  if (status === 502 || detail === "upstream_unavailable") {
    return "后台服务还没准备好，请先启动后端核心服务。";
  }
  if (status === 504 || detail === "upstream_timeout") {
    return "系统响应超时，请稍后再试。";
  }
  if (status === 404 || detail === "patient_not_found") {
    return "未找到该患者基础档案，请从床位或草稿列表重新进入。";
  }
  if (detail === "draft_not_found") {
    return "这份草稿可能已经被归档或被其他终端更新，请返回上一页刷新。";
  }
  if (detail && detail !== "upstream_error") {
    return detail;
  }

  const message = String((error as any)?.message || "");
  if (message === "Network Error") {
    return "网络连接失败，请确认手机和电脑在同一局域网，或直接改用本机地址。";
  }
  if (message.includes("timeout of")) {
    return "请求超时，请确认当前网关地址可达，并检查后端服务是否已启动；如果只有智能模块持续超时，再去“我的-运行状态”检查本地模型。";
  }
  if (message.includes("failed to fetch") || message.includes("stream disconnected before completion")) {
    return "远端连接中断，本次会话已自动保留。请稍后重试，或先到“我的-运行状态”确认模型服务与网络状态。";
  }
  if (message) {
    return message;
  }
  return fallback;
}

function shouldUseLocalFallback(error: unknown) {
  const status = Number((error as any)?.response?.status || 0);
  const detail = extractDetail((error as any)?.response?.data);
  const message = String((error as any)?.message || "").toLowerCase();

  return (
    status === 0 ||
    status === 502 ||
    status === 503 ||
    status === 504 ||
    detail === "upstream_error" ||
    detail === "upstream_timeout" ||
    detail === "upstream_unavailable" ||
    message.includes("network error") ||
    message.includes("failed to fetch") ||
    message.includes("timeout of") ||
    message.includes("stream disconnected before completion") ||
    message.includes("socket hang up") ||
    message.includes("econnrefused")
  );
}

async function withLocalFallback<T>(loader: () => Promise<T>, fallback: () => Promise<T> | T): Promise<T> {
  try {
    return await loader();
  } catch (error) {
    if (!shouldUseLocalFallback(error)) {
      throw error;
    }
    try {
      return await fallback();
    } catch {
      throw error;
    }
  }
}

function buildAiChatBody(payload: {
  mode: AIChatMode;
  selectedModel?: string;
  clusterProfile?: string;
  patientId?: string;
  bedNo?: string;
  conversationId?: string;
  departmentId?: string;
  userInput: string;
  missionTitle?: string;
  successCriteria?: string[];
  operatorNotes?: string;
  attachments?: string[];
  requestedBy?: string;
  agentMode?: string;
  executionProfile?: AIExecutionProfile;
}) {
  return {
    mode: payload.mode,
    selected_model: payload.selectedModel,
    cluster_profile: payload.clusterProfile || "nursing_default_cluster",
    patient_id: payload.patientId,
    bed_no: payload.bedNo,
    conversation_id: payload.conversationId,
    department_id: payload.departmentId,
    user_input: payload.userInput,
    mission_title: payload.missionTitle,
    success_criteria: payload.successCriteria || [],
    operator_notes: payload.operatorNotes,
    attachments: payload.attachments || [],
    requested_by: payload.requestedBy,
    agent_mode: payload.agentMode,
    execution_profile: payload.executionProfile,
  };
}

function getAiChatTimeoutMs(payload: {
  mode: AIChatMode;
  userInput: string;
  operatorNotes?: string;
  executionProfile?: AIExecutionProfile;
}) {
  const textWeight = String(payload.userInput || "").length + String(payload.operatorNotes || "").length;
  const dynamicExtra = Math.min(45_000, Math.ceil(textWeight / 450) * 5_000);

  if (payload.mode === "agent_cluster") {
    const base = payload.executionProfile === "full_loop" ? 90_000 : 70_000;
    return base + dynamicExtra;
  }

  return 45_000 + Math.min(20_000, dynamicExtra);
}

function shouldRetryAiChat(error: unknown) {
  const status = Number((error as any)?.response?.status || 0);
  const message = String((error as any)?.message || "").toLowerCase();
  return (
    status === 0 &&
    (message.includes("network error") ||
      message.includes("timeout of") ||
      message.includes("failed to fetch") ||
      message.includes("stream disconnected before completion"))
  );
}

function toStringArray(items: unknown): string[] {
  return Array.isArray(items) ? items.map((item) => String(item)) : [];
}

function sanitizePendingTasks(items: unknown): string[] {
  const seen = new Set<string>();
  return toStringArray(items)
    .map((item) => item.trim())
    .filter((item) => {
      if (!item) {
        return false;
      }
      if (item.startsWith("文书状态：") || item.startsWith("最新文书：")) {
        return false;
      }
      if (seen.has(item)) {
        return false;
      }
      seen.add(item);
      return true;
    });
}

function mapBedOverview(data: any): BedOverview {
  return {
    id: String(data?.id || ""),
    department_id: String(data?.department_id || ""),
    bed_no: String(data?.bed_no || ""),
    room_no: data?.room_no ? String(data.room_no) : undefined,
    status: String(data?.status || ""),
    current_patient_id: data?.current_patient_id ? String(data.current_patient_id) : undefined,
    patient_name: data?.patient_name ? String(data.patient_name) : undefined,
    risk_tags: toStringArray(data?.risk_tags),
    pending_tasks: sanitizePendingTasks(data?.pending_tasks),
    nursing_level: data?.nursing_level
      ? String(data.nursing_level)
      : data?.nursing_grade
      ? String(data.nursing_grade)
      : data?.nursing_class
      ? String(data.nursing_class)
      : undefined,
    risk_level: data?.risk_level ? String(data.risk_level) : undefined,
    risk_score: data?.risk_score !== undefined ? Number(data.risk_score) : undefined,
    risk_reason: data?.risk_reason ? String(data.risk_reason) : undefined,
    latest_document_sync: data?.latest_document_sync ? String(data.latest_document_sync) : undefined,
  };
}

function mapPatientContext(data: any): PatientContext {
  const observations = Array.isArray(data?.latest_observations)
    ? data.latest_observations.map((item: any) => ({
        name: String(item?.name || ""),
        value: String(item?.value || ""),
        abnormal_flag: item?.abnormal_flag ? String(item.abnormal_flag) : undefined,
      }))
    : [];
  return {
    patient_id: String(data?.patient_id || ""),
    patient_name: data?.patient_name ? String(data.patient_name) : undefined,
    bed_no: data?.bed_no ? String(data.bed_no) : undefined,
    encounter_id: data?.encounter_id ? String(data.encounter_id) : undefined,
    diagnoses: toStringArray(data?.diagnoses),
    risk_tags: toStringArray(data?.risk_tags),
    pending_tasks: sanitizePendingTasks(data?.pending_tasks),
    nursing_level: data?.nursing_level
      ? String(data.nursing_level)
      : data?.nursing_grade
      ? String(data.nursing_grade)
      : data?.nursing_class
      ? String(data.nursing_class)
      : undefined,
    risk_level: data?.risk_level ? String(data.risk_level) : undefined,
    risk_score: data?.risk_score !== undefined ? Number(data.risk_score) : undefined,
    risk_reason: data?.risk_reason ? String(data.risk_reason) : undefined,
    latest_observations: observations,
    latest_document_sync: data?.latest_document_sync ? String(data.latest_document_sync) : undefined,
    latest_document_status: data?.latest_document_status ? String(data.latest_document_status) : undefined,
    latest_document_type: data?.latest_document_type ? String(data.latest_document_type) : undefined,
    latest_document_excerpt: data?.latest_document_excerpt ? String(data.latest_document_excerpt) : undefined,
    latest_document_updated_at: data?.latest_document_updated_at ? String(data.latest_document_updated_at) : undefined,
  };
}

function toRecord(value: unknown): Record<string, unknown> | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return undefined;
  }
  return value as Record<string, unknown>;
}

const mapRecommendation = (data: any): RecommendationResult => ({
  id: String(data?.id || ""),
  patient_id: String(data?.patient_id || ""),
  summary: String(data?.summary || ""),
  findings: Array.isArray(data?.findings) ? data.findings.map((item: any) => String(item)) : [],
  recommendations: Array.isArray(data?.recommendations) ? data.recommendations : [],
  confidence: Number(data?.confidence ?? 0),
  review_required: Boolean(data?.review_required ?? true),
  metadata: data?.metadata || {},
});

const mapAgentStep = (data: any) => ({
  agent: String(data?.agent || ""),
  status: String(data?.status || ""),
  note: data?.note ? String(data.note) : undefined,
  input: toRecord(data?.input),
  output: toRecord(data?.output),
});

const mapAgentPlanItem = (data: any) => ({
  id: String(data?.id || ""),
  title: String(data?.title || ""),
  tool: data?.tool ? String(data.tool) : undefined,
  reason: data?.reason ? String(data.reason) : undefined,
  status: String(data?.status || "pending"),
  auto_runnable: data?.auto_runnable !== undefined ? Boolean(data.auto_runnable) : undefined,
});

const mapAgentArtifact = (data: any) => ({
  kind: String(data?.kind || ""),
  title: String(data?.title || ""),
  status: String(data?.status || "created"),
  reference_id: data?.reference_id ? String(data.reference_id) : undefined,
  summary: data?.summary ? String(data.summary) : undefined,
  metadata: toRecord(data?.metadata),
});

const mapAgentToolExecution = (data: any) => ({
  item_id: String(data?.item_id || ""),
  title: String(data?.title || ""),
  tool: data?.tool ? String(data.tool) : undefined,
  agent: String(data?.agent || ""),
  status: String(data?.status || ""),
  attempts: Number(data?.attempts ?? 1),
  retryable: Boolean(data?.retryable),
  started_at: String(data?.started_at || new Date().toISOString()),
  finished_at: String(data?.finished_at || new Date().toISOString()),
  output: toRecord(data?.output),
  error: data?.error ? String(data.error) : undefined,
});

const mapAgentApproval = (data: any) => ({
  id: String(data?.id || ""),
  item_id: String(data?.item_id || ""),
  tool_id: data?.tool_id ? String(data.tool_id) : undefined,
  title: String(data?.title || ""),
  reason: data?.reason ? String(data.reason) : undefined,
  status: String(data?.status || "pending"),
  created_at: String(data?.created_at || ""),
  decided_at: data?.decided_at ? String(data.decided_at) : undefined,
  decided_by: data?.decided_by ? String(data.decided_by) : undefined,
  comment: data?.comment ? String(data.comment) : undefined,
  metadata: toRecord(data?.metadata),
});

const mapAgentMemory = (data: any) => {
  const source = toRecord(data);
  if (!source) {
    return undefined;
  }
  return {
    conversation_summary: String(source.conversation_summary || ""),
    patient_facts: toStringArray(source.patient_facts),
    unresolved_tasks: toStringArray(source.unresolved_tasks),
    last_actions: toStringArray(source.last_actions),
    user_preferences: toStringArray(source.user_preferences),
  };
};

const mapRoleLane = (data: any) => ({
  id: String(data?.id || ""),
  title: String(data?.title || ""),
  role: String(data?.role || ""),
  focus: String(data?.focus || ""),
  status: String(data?.status || "recommended"),
  reason: data?.reason ? String(data.reason) : undefined,
  next_action: data?.next_action ? String(data.next_action) : undefined,
});

const mapServiceRelayStage = (data: any) => ({
  id: String(data?.id || ""),
  title: String(data?.title || ""),
  status: String(data?.status || "pending"),
  owner: String(data?.owner || ""),
  summary: data?.summary ? String(data.summary) : undefined,
});

const mapPatientStateCapsule = (data: any) => {
  const source = toRecord(data);
  if (!source) {
    return undefined;
  }
  return {
    patient_id: source.patient_id ? String(source.patient_id) : undefined,
    version: source.version ? String(source.version) : undefined,
    event_summary: toStringArray(source.event_summary),
    time_axis: toStringArray(source.time_axis),
    data_layers: toStringArray(source.data_layers),
    risk_factors: toStringArray(source.risk_factors),
  };
};

const mapCareGraph = (data: any) => {
  const source = toRecord(data);
  if (!source) {
    return undefined;
  }
  return {
    nodes: toStringArray(source.nodes),
    edges: toStringArray(source.edges),
    dynamic_updates: toStringArray(source.dynamic_updates),
  };
};

const mapReasoningCheckpoint = (data: any) => ({
  mode: String(data?.mode || ""),
  title: String(data?.title || ""),
  summary: String(data?.summary || ""),
  confidence: data?.confidence !== undefined ? Number(data.confidence) : undefined,
});

const mapWorkflowOutput = (data: any) => ({
  workflow_type: String(data?.workflow_type || ""),
  summary: String(data?.summary || ""),
  findings: toStringArray(data?.findings),
  recommendations: Array.isArray(data?.recommendations) ? data.recommendations : [],
  confidence: Number(data?.confidence ?? 0),
  review_required: Boolean(data?.review_required ?? true),
  patient_id: data?.patient_id ? String(data.patient_id) : undefined,
  patient_name: data?.patient_name ? String(data.patient_name) : undefined,
  bed_no: data?.bed_no ? String(data.bed_no) : undefined,
  steps: Array.isArray(data?.steps) ? data.steps.map(mapAgentStep) : [],
  run_id: data?.run_id ? String(data.run_id) : undefined,
  runtime_engine: data?.runtime_engine ? String(data.runtime_engine) : undefined,
  agent_goal: data?.agent_goal ? String(data.agent_goal) : undefined,
  agent_mode: String(data?.agent_mode || "workflow"),
  execution_profile: data?.execution_profile ? String(data.execution_profile) : undefined,
  mission_title: data?.mission_title ? String(data.mission_title) : undefined,
  success_criteria: toStringArray(data?.success_criteria),
  plan: Array.isArray(data?.plan) ? data.plan.map(mapAgentPlanItem) : [],
  memory: mapAgentMemory(data?.memory),
  artifacts: Array.isArray(data?.artifacts) ? data.artifacts.map(mapAgentArtifact) : [],
  specialist_profiles: Array.isArray(data?.specialist_profiles) ? data.specialist_profiles.map(mapRoleLane) : [],
  hybrid_care_path: Array.isArray(data?.hybrid_care_path) ? data.hybrid_care_path.map(mapServiceRelayStage) : [],
  data_capsule: mapPatientStateCapsule(data?.data_capsule),
  health_graph: mapCareGraph(data?.health_graph),
  reasoning_cards: Array.isArray(data?.reasoning_cards) ? data.reasoning_cards.map(mapReasoningCheckpoint) : [],
  tool_executions: Array.isArray(data?.tool_executions) ? data.tool_executions.map(mapAgentToolExecution) : [],
  pending_approvals: Array.isArray(data?.pending_approvals) ? data.pending_approvals.map(mapAgentApproval) : [],
  next_actions: toStringArray(data?.next_actions),
  created_at: String(data?.created_at || new Date().toISOString()),
});

const mapAgentRunRecord = (data: any): AgentRunRecord => ({
  id: String(data?.id || ""),
  status: String(data?.status || "running"),
  workflow_type: String(data?.workflow_type || data?.request?.workflow_type || ""),
  runtime_engine: String(data?.runtime_engine || "state_machine"),
  request: {
    workflow_type: String(data?.request?.workflow_type || data?.workflow_type || ""),
    patient_id: data?.request?.patient_id ? String(data.request.patient_id) : undefined,
    conversation_id: data?.request?.conversation_id ? String(data.request.conversation_id) : undefined,
    department_id: data?.request?.department_id ? String(data.request.department_id) : undefined,
    bed_no: data?.request?.bed_no ? String(data.request.bed_no) : undefined,
    user_input: data?.request?.user_input ? String(data.request.user_input) : undefined,
    mission_title: data?.request?.mission_title ? String(data.request.mission_title) : undefined,
    success_criteria: toStringArray(data?.request?.success_criteria),
    operator_notes: data?.request?.operator_notes ? String(data.request.operator_notes) : undefined,
    requested_by: data?.request?.requested_by ? String(data.request.requested_by) : undefined,
    agent_mode: data?.request?.agent_mode ? String(data.request.agent_mode) : undefined,
    execution_profile: data?.request?.execution_profile ? String(data.request.execution_profile) : undefined,
    attachments_count: Number(data?.request?.attachments_count ?? 0),
    approved_actions: toStringArray(data?.request?.approved_actions),
    rejected_actions: toStringArray(data?.request?.rejected_actions),
  },
  patient_id: data?.patient_id ? String(data.patient_id) : undefined,
  patient_name: data?.patient_name ? String(data.patient_name) : undefined,
  bed_no: data?.bed_no ? String(data.bed_no) : undefined,
  conversation_id: data?.conversation_id ? String(data.conversation_id) : undefined,
  agent_goal: data?.agent_goal ? String(data.agent_goal) : undefined,
  agent_mode: String(data?.agent_mode || "workflow"),
  summary: data?.summary ? String(data.summary) : undefined,
  plan: Array.isArray(data?.plan) ? data.plan.map(mapAgentPlanItem) : [],
  memory: mapAgentMemory(data?.memory),
  artifacts: Array.isArray(data?.artifacts) ? data.artifacts.map(mapAgentArtifact) : [],
  specialist_profiles: Array.isArray(data?.specialist_profiles) ? data.specialist_profiles.map(mapRoleLane) : [],
  hybrid_care_path: Array.isArray(data?.hybrid_care_path) ? data.hybrid_care_path.map(mapServiceRelayStage) : [],
  data_capsule: mapPatientStateCapsule(data?.data_capsule),
  health_graph: mapCareGraph(data?.health_graph),
  reasoning_cards: Array.isArray(data?.reasoning_cards) ? data.reasoning_cards.map(mapReasoningCheckpoint) : [],
  next_actions: toStringArray(data?.next_actions),
  steps: Array.isArray(data?.steps) ? data.steps.map(mapAgentStep) : [],
  tool_executions: Array.isArray(data?.tool_executions) ? data.tool_executions.map(mapAgentToolExecution) : [],
  pending_approvals: Array.isArray(data?.pending_approvals) ? data.pending_approvals.map(mapAgentApproval) : [],
  retry_available: Boolean(data?.retry_available),
  error: data?.error ? String(data.error) : undefined,
  created_at: String(data?.created_at || new Date().toISOString()),
  updated_at: String(data?.updated_at || new Date().toISOString()),
  completed_at: data?.completed_at ? String(data.completed_at) : undefined,
});

const mapAiRuntimeStatus = (data: any): AIRuntimeStatus => ({
  configured_engine: String(data?.configured_engine || "state_machine"),
  active_engine: String(data?.active_engine || "state_machine"),
  langgraph_available: Boolean(data?.langgraph_available),
  override_enabled: Boolean(data?.override_enabled),
  fallback_reason: String(data?.fallback_reason || ""),
  planner_llm_enabled: Boolean(data?.planner_llm_enabled),
  planner_timeout_sec: Number(data?.planner_timeout_sec ?? 0),
  planner_max_steps: Number(data?.planner_max_steps ?? 0),
  local_model_service_reachable: Boolean(data?.local_model_service_reachable),
  available_local_models: toStringArray(data?.available_local_models),
  local_model_aliases: toRecord(data?.local_model_aliases) as AIRuntimeStatus["local_model_aliases"],
  approval_required_tools: toStringArray(data?.approval_required_tools),
  task_queue: toRecord(data?.task_queue) as AIRuntimeStatus["task_queue"],
});

const mapQueueTask = (data: any): AgentQueueTask => ({
  id: String(data?.id || ""),
  status: String(data?.status || "queued"),
  payload: {
    workflow_type: String(data?.payload?.workflow_type || data?.workflow_type || ""),
    patient_id: data?.payload?.patient_id ? String(data.payload.patient_id) : undefined,
    conversation_id: data?.payload?.conversation_id ? String(data.payload.conversation_id) : undefined,
    department_id: data?.payload?.department_id ? String(data.payload.department_id) : undefined,
    bed_no: data?.payload?.bed_no ? String(data.payload.bed_no) : undefined,
    user_input: data?.payload?.user_input ? String(data.payload.user_input) : undefined,
    mission_title: data?.payload?.mission_title ? String(data.payload.mission_title) : undefined,
    success_criteria: toStringArray(data?.payload?.success_criteria),
    operator_notes: data?.payload?.operator_notes ? String(data.payload.operator_notes) : undefined,
    attachments: toStringArray(data?.payload?.attachments),
    requested_by: data?.payload?.requested_by ? String(data.payload.requested_by) : undefined,
    agent_mode: data?.payload?.agent_mode ? String(data.payload.agent_mode) : undefined,
    execution_profile: data?.payload?.execution_profile ? String(data.payload.execution_profile) : undefined,
    approved_actions: toStringArray(data?.payload?.approved_actions),
    rejected_actions: toStringArray(data?.payload?.rejected_actions),
  },
  workflow_type: String(data?.workflow_type || data?.payload?.workflow_type || ""),
  requested_engine: data?.requested_engine ? String(data.requested_engine) : undefined,
  runtime_engine: data?.runtime_engine ? String(data.runtime_engine) : undefined,
  priority: Number(data?.priority ?? 100),
  run_id: data?.run_id ? String(data.run_id) : undefined,
  summary: data?.summary ? String(data.summary) : undefined,
  approvals: Array.isArray(data?.approvals) ? data.approvals.map(mapAgentApproval) : [],
  last_output: data?.last_output ? mapWorkflowOutput(data.last_output) : undefined,
  error: data?.error ? String(data.error) : undefined,
  attempt_count: Number(data?.attempt_count ?? 0),
  resume_count: Number(data?.resume_count ?? 0),
  created_at: String(data?.created_at || new Date().toISOString()),
  updated_at: String(data?.updated_at || new Date().toISOString()),
  started_at: data?.started_at ? String(data.started_at) : undefined,
  completed_at: data?.completed_at ? String(data.completed_at) : undefined,
});

const mapConversationHistory = (items: any[]): ConversationHistoryItem[] =>
  (Array.isArray(items) ? items : []).map((item) => ({
    id: String(item?.id || ""),
    source: String(item?.source || ""),
    workflow_type: String(item?.workflow_type || ""),
    patient_id: item?.patient_id ? String(item.patient_id) : undefined,
    conversation_id: item?.conversation_id ? String(item.conversation_id) : undefined,
    user_input: item?.user_input ? String(item.user_input) : undefined,
    summary: String(item?.summary || ""),
    created_at: String(item?.created_at || ""),
    confidence: item?.confidence !== undefined ? Number(item.confidence) : undefined,
    review_required: item?.review_required !== undefined ? Boolean(item.review_required) : undefined,
    run_id: item?.run_id ? String(item.run_id) : undefined,
    runtime_engine: item?.runtime_engine ? String(item.runtime_engine) : undefined,
    findings: toStringArray(item?.findings),
    recommendations: Array.isArray(item?.recommendations) ? item.recommendations : [],
    steps: Array.isArray(item?.steps) ? item.steps.map(mapAgentStep) : [],
    agent_goal: item?.agent_goal ? String(item.agent_goal) : undefined,
    agent_mode: item?.agent_mode ? String(item.agent_mode) : undefined,
    execution_profile: item?.execution_profile ? String(item.execution_profile) : undefined,
    mission_title: item?.mission_title ? String(item.mission_title) : undefined,
    success_criteria: toStringArray(item?.success_criteria),
    plan: Array.isArray(item?.plan) ? item.plan.map(mapAgentPlanItem) : [],
    memory: mapAgentMemory(item?.memory),
    artifacts: Array.isArray(item?.artifacts) ? item.artifacts.map(mapAgentArtifact) : [],
    specialist_profiles: Array.isArray(item?.specialist_profiles) ? item.specialist_profiles.map(mapRoleLane) : [],
    hybrid_care_path: Array.isArray(item?.hybrid_care_path) ? item.hybrid_care_path.map(mapServiceRelayStage) : [],
    data_capsule: mapPatientStateCapsule(item?.data_capsule),
    health_graph: mapCareGraph(item?.health_graph),
    reasoning_cards: Array.isArray(item?.reasoning_cards) ? item.reasoning_cards.map(mapReasoningCheckpoint) : [],
    pending_approvals: Array.isArray(item?.pending_approvals) ? item.pending_approvals.map(mapAgentApproval) : [],
    next_actions: toStringArray(item?.next_actions),
  }));

const mapThreadHistory = (items: any[]): CollaborationThreadHistoryItem[] =>
  (Array.isArray(items) ? items : []).map((item) => ({
    thread: {
      id: String(item?.thread?.id || ""),
      patient_id: item?.thread?.patient_id ? String(item.thread.patient_id) : undefined,
      encounter_id: item?.thread?.encounter_id ? String(item.thread.encounter_id) : undefined,
      thread_type: String(item?.thread?.thread_type || "discussion"),
      title: String(item?.thread?.title || ""),
      created_by: item?.thread?.created_by ? String(item.thread.created_by) : undefined,
      status: String(item?.thread?.status || "open"),
      created_at: String(item?.thread?.created_at || ""),
      updated_at: String(item?.thread?.updated_at || ""),
    },
    latest_message: item?.latest_message
      ? {
          id: String(item.latest_message.id || ""),
          thread_id: String(item.latest_message.thread_id || ""),
          sender_id: item.latest_message.sender_id ? String(item.latest_message.sender_id) : undefined,
          message_type: String(item.latest_message.message_type || "text"),
          content: String(item.latest_message.content || ""),
          attachment_refs: Array.isArray(item.latest_message.attachment_refs)
            ? item.latest_message.attachment_refs.map((x: any) => String(x))
            : [],
          ai_generated: Boolean(item.latest_message.ai_generated),
          created_at: String(item.latest_message.created_at || ""),
        }
      : null,
    message_count: Number(item?.message_count ?? 0),
  }));

const mapAccount = (item: any): CollabAccount => ({
  id: String(item?.id || ""),
  account: String(item?.account || ""),
  full_name: String(item?.full_name || ""),
  role_code: String(item?.role_code || ""),
  department: item?.department ? String(item.department) : undefined,
  title: item?.title ? String(item.title) : undefined,
});

const mapDirectSession = (item: any): DirectSession => ({
  id: String(item?.id || ""),
  user_id: String(item?.user_id || ""),
  contact_user_id: String(item?.contact_user_id || ""),
  patient_id: item?.patient_id ? String(item.patient_id) : undefined,
  status: String(item?.status || "open"),
  created_at: String(item?.created_at || ""),
  updated_at: String(item?.updated_at || ""),
  latest_message: item?.latest_message
    ? {
        id: String(item.latest_message.id || ""),
        thread_id: String(item.latest_message.thread_id || ""),
        sender_id: item.latest_message.sender_id ? String(item.latest_message.sender_id) : undefined,
        message_type: String(item.latest_message.message_type || "text"),
        content: String(item.latest_message.content || ""),
        attachment_refs: Array.isArray(item.latest_message.attachment_refs)
          ? item.latest_message.attachment_refs.map((x: any) => String(x))
          : [],
        ai_generated: Boolean(item.latest_message.ai_generated),
        created_at: String(item.latest_message.created_at || ""),
      }
    : null,
  unread_count: Number(item?.unread_count ?? 0),
  contact: item?.contact ? mapAccount(item.contact) : undefined,
});

const mapAiChatResponse = (data: any): AIChatResponse => ({
  mode: data?.mode === "single_model" ? "single_model" : "agent_cluster",
  selected_model: data?.selected_model ? String(data.selected_model) : undefined,
  cluster_profile: data?.cluster_profile ? String(data.cluster_profile) : undefined,
  conversation_id: data?.conversation_id ? String(data.conversation_id) : undefined,
  patient_id: data?.patient_id ? String(data.patient_id) : undefined,
  patient_name: data?.patient_name ? String(data.patient_name) : undefined,
  bed_no: data?.bed_no ? String(data.bed_no) : undefined,
  run_id: data?.run_id ? String(data.run_id) : undefined,
  runtime_engine: data?.runtime_engine ? String(data.runtime_engine) : undefined,
  workflow_type: String(data?.workflow_type || ""),
  summary: String(data?.summary || ""),
  findings: toStringArray(data?.findings),
  recommendations: Array.isArray(data?.recommendations) ? data.recommendations : [],
  confidence: Number(data?.confidence ?? 0),
  review_required: Boolean(data?.review_required ?? true),
  steps: Array.isArray(data?.steps) ? data.steps.map(mapAgentStep) : [],
  model_plan: Array.isArray(data?.model_plan)
    ? data.model_plan.map((x: any) => ({
        model_id: String(x?.model_id || ""),
        model_name: String(x?.model_name || ""),
        role: String(x?.role || ""),
        task: String(x?.task || ""),
        enabled: Boolean(x?.enabled ?? true),
      }))
    : [],
  agent_goal: data?.agent_goal ? String(data.agent_goal) : undefined,
  agent_mode: String(data?.agent_mode || "workflow"),
  mission_title: data?.mission_title ? String(data.mission_title) : undefined,
  success_criteria: toStringArray(data?.success_criteria),
  plan: Array.isArray(data?.plan) ? data.plan.map(mapAgentPlanItem) : [],
  memory: mapAgentMemory(data?.memory),
  artifacts: Array.isArray(data?.artifacts) ? data.artifacts.map(mapAgentArtifact) : [],
  specialist_profiles: Array.isArray(data?.specialist_profiles) ? data.specialist_profiles.map(mapRoleLane) : [],
  hybrid_care_path: Array.isArray(data?.hybrid_care_path) ? data.hybrid_care_path.map(mapServiceRelayStage) : [],
  data_capsule: mapPatientStateCapsule(data?.data_capsule),
  health_graph: mapCareGraph(data?.health_graph),
  reasoning_cards: Array.isArray(data?.reasoning_cards) ? data.reasoning_cards.map(mapReasoningCheckpoint) : [],
  pending_approvals: Array.isArray(data?.pending_approvals) ? data.pending_approvals.map(mapAgentApproval) : [],
  next_actions: toStringArray(data?.next_actions),
  created_at: String(data?.created_at || new Date().toISOString()),
  execution_profile: data?.execution_profile ? String(data.execution_profile) : undefined,
});

const mapOrder = (row: any): ClinicalOrder => ({
  id: String(row?.id || ""),
  patient_id: String(row?.patient_id || ""),
  encounter_id: row?.encounter_id ? String(row.encounter_id) : undefined,
  order_no: String(row?.order_no || ""),
  order_type: String(row?.order_type || ""),
  title: String(row?.title || ""),
  instruction: String(row?.instruction || ""),
  route: row?.route ? String(row.route) : undefined,
  dosage: row?.dosage ? String(row.dosage) : undefined,
  frequency: row?.frequency ? String(row.frequency) : undefined,
  priority: String(row?.priority || "P2"),
  status: String(row?.status || "pending"),
  ordered_by: row?.ordered_by ? String(row.ordered_by) : undefined,
  ordered_at: row?.ordered_at ? String(row.ordered_at) : undefined,
  due_at: row?.due_at ? String(row.due_at) : undefined,
  requires_double_check: Boolean(row?.requires_double_check),
  check_by: row?.check_by ? String(row.check_by) : undefined,
  check_at: row?.check_at ? String(row.check_at) : undefined,
  executed_by: row?.executed_by ? String(row.executed_by) : undefined,
  executed_at: row?.executed_at ? String(row.executed_at) : undefined,
  execution_note: row?.execution_note ? String(row.execution_note) : undefined,
  exception_reason: row?.exception_reason ? String(row.exception_reason) : undefined,
  risk_hints: Array.isArray(row?.risk_hints) ? row.risk_hints.map((x: any) => String(x)) : [],
  audit_trail: Array.isArray(row?.audit_trail)
    ? row.audit_trail.map((x: any) => ({
        action: String(x?.action || ""),
        actor: String(x?.actor || ""),
        note: x?.note ? String(x.note) : undefined,
        created_at: String(x?.created_at || ""),
      }))
    : [],
});

const mapOrderList = (data: any): OrderListOut => ({
  patient_id: String(data?.patient_id || ""),
  stats: {
    pending: Number(data?.stats?.pending ?? 0),
    due_30m: Number(data?.stats?.due_30m ?? 0),
    overdue: Number(data?.stats?.overdue ?? 0),
    high_alert: Number(data?.stats?.high_alert ?? 0),
  },
  orders: Array.isArray(data?.orders) ? data.orders.map(mapOrder) : [],
});

const WARD_FALLBACK_IDS = ["dep-card-01", "dep-icu-01", "dep-ward-01"] as const;

function uniqKeepOrder(items: string[]): string[] {
  const hit = new Set<string>();
  const output: string[] = [];
  items.forEach((raw) => {
    const value = String(raw || "").trim();
    if (!value || hit.has(value)) {
      return;
    }
    hit.add(value);
    output.push(value);
  });
  return output;
}

export const api = {
  async login(username: string, password: string) {
    if (isMockMode) {
      return mockApi.login(username, password);
    }
    const { data } = await httpClient.post("/api/auth/login", { username, password });
    return data;
  },

  async register(payload: {
    username: string;
    password: string;
    full_name: string;
    role_code?: string;
    phone?: string;
  }) {
    if (isMockMode) {
      return mockApi.register(payload);
    }
    const { data } = await httpClient.post("/api/auth/register", payload);
    return data;
  },

  async getWardBeds(departmentId: string) {
    if (isMockMode) {
      return (await mockApi.getWardBeds()).map(mapBedOverview);
    }
    return withLocalFallback(
      async () => {
        const requested = String(departmentId || "").trim() || "dep-card-01";
        const candidateIds = uniqKeepOrder([requested, "dep-card-01", ...WARD_FALLBACK_IDS]);
        let lastError: unknown = null;

        for (const depId of candidateIds) {
          try {
            const { data } = await httpClient.get(`/api/wards/${depId}/beds`);
            if (Array.isArray(data) && data.length > 0) {
              return data.map(mapBedOverview);
            }
          } catch (err) {
            lastError = err;
          }
        }

        if (lastError) {
          throw lastError;
        }
        return [];
      },
      async () => (await mockApi.getWardBeds()).map(mapBedOverview)
    );
  },

  async getPatient(patientId: string): Promise<Patient> {
    if (isMockMode) {
      return mockApi.getPatient(patientId);
    }
    return withLocalFallback(
      async () => {
        const { data } = await httpClient.get(`/api/patients/${patientId}`);
        return data;
      },
      () => mockApi.getPatient(patientId)
    );
  },

  async getPatientContext(patientId: string, requestedBy?: string): Promise<PatientContext> {
    if (isMockMode) {
      return mapPatientContext(await mockApi.getPatientContext(patientId));
    }
    return withLocalFallback(
      async () => {
        const { data } = await httpClient.get(`/api/patients/${patientId}/context`, {
          params: {
            requested_by: requestedBy,
          },
        });
        return mapPatientContext(data);
      },
      async () => mapPatientContext(await mockApi.getPatientContext(patientId))
    );
  },

  async getBedContext(
    bedNo: string,
    options?: { departmentId?: string; requestedBy?: string }
  ): Promise<PatientContext> {
    if (isMockMode) {
      return mapPatientContext(await (mockApi as any).getBedContext(bedNo, options?.departmentId));
    }
    return withLocalFallback(
      async () => {
        const { data } = await httpClient.get(`/api/beds/${encodeURIComponent(bedNo)}/context`, {
          params: {
            department_id: options?.departmentId,
            requested_by: options?.requestedBy,
          },
        });
        return mapPatientContext(data);
      },
      async () => mapPatientContext(await (mockApi as any).getBedContext(bedNo, options?.departmentId))
    );
  },

  async getPatientOrders(patientId: string): Promise<OrderListOut> {
    if (isMockMode) {
      return mockApi.getPatientOrders(patientId);
    }
    return withLocalFallback(
      async () => {
        const { data } = await httpClient.get(`/api/orders/patients/${patientId}`);
        return mapOrderList(data);
      },
      () => mockApi.getPatientOrders(patientId)
    );
  },

  async getPatientOrderHistory(patientId: string, limit = 80): Promise<ClinicalOrder[]> {
    if (isMockMode) {
      return mockApi.getPatientOrderHistory(patientId, limit);
    }
    const { data } = await httpClient.get(`/api/orders/patients/${patientId}/history`, {
      params: { limit },
    });
    return Array.isArray(data) ? data.map(mapOrder) : [];
  },

  async doubleCheckOrder(orderId: string, checkedBy: string, note?: string): Promise<ClinicalOrder> {
    if (isMockMode) {
      return mockApi.doubleCheckOrder(orderId, checkedBy, note);
    }
    const { data } = await httpClient.post(`/api/orders/${orderId}/double-check`, {
      checked_by: checkedBy,
      note,
    });
    return mapOrder(data);
  },

  async executeOrder(orderId: string, executedBy: string, note?: string): Promise<ClinicalOrder> {
    if (isMockMode) {
      return mockApi.executeOrder(orderId, executedBy, note);
    }
    const { data } = await httpClient.post(`/api/orders/${orderId}/execute`, {
      executed_by: executedBy,
      note,
    });
    return mapOrder(data);
  },

  async reportOrderException(orderId: string, reportedBy: string, reason: string): Promise<ClinicalOrder> {
    if (isMockMode) {
      return mockApi.reportOrderException(orderId, reportedBy, reason);
    }
    const { data } = await httpClient.post(`/api/orders/${orderId}/exception`, {
      reported_by: reportedBy,
      reason,
    });
    return mapOrder(data);
  },

  async createOrderRequest(payload: {
    patientId: string;
    requestedBy: string;
    title: string;
    details: string;
    priority?: string;
  }): Promise<ClinicalOrder> {
    if (isMockMode) {
      if ((mockApi as any).createOrderRequest) {
        return (mockApi as any).createOrderRequest(payload);
      }
      throw new Error("mock_not_implemented");
    }
    const { data } = await httpClient.post("/api/orders/request", {
      patient_id: payload.patientId,
      requested_by: payload.requestedBy,
      title: payload.title,
      details: payload.details,
      priority: payload.priority || "P2",
    });
    return mapOrder(data);
  },

  async getAiModels(): Promise<AIModelsCatalog> {
    if (isMockMode) {
      return mockApi.getAiModels();
    }
    return withLocalFallback(
      async () => {
        const { data } = await httpClient.get("/api/ai/models");
        return data;
      },
      () => mockApi.getAiModels()
    );
  },

  async getAiRuntimeStatus(): Promise<AIRuntimeStatus> {
    if (isMockMode) {
      return (mockApi as any).getAiRuntimeStatus();
    }
    return withLocalFallback(
      async () => {
        const { data } = await httpClient.get("/api/ai/runtime");
        return mapAiRuntimeStatus(data);
      },
      () => (mockApi as any).getAiRuntimeStatus()
    );
  },

  async setAiRuntimeEngine(engine: "state_machine" | "langgraph"): Promise<AIRuntimeStatus> {
    if (isMockMode) {
      return (mockApi as any).setAiRuntimeEngine(engine);
    }
    const { data } = await httpClient.post("/api/ai/runtime", { engine });
    return mapAiRuntimeStatus(data);
  },

  async clearAiRuntimeEngine(): Promise<AIRuntimeStatus> {
    if (isMockMode) {
      return (mockApi as any).clearAiRuntimeEngine();
    }
    const { data } = await httpClient.delete("/api/ai/runtime");
    return mapAiRuntimeStatus(data);
  },

  async previewPatientScope(payload: {
    userInput: string;
    patientId?: string;
    bedNo?: string;
    departmentId?: string;
    requestedBy?: string;
  }): Promise<PatientScopePreview> {
    if (isMockMode) {
      return (mockApi as any).previewPatientScope(payload);
    }
    const { data } = await httpClient.post("/api/ai/scope/preview", {
      workflow_type: "voice_inquiry",
      patient_id: payload.patientId,
      bed_no: payload.bedNo,
      department_id: payload.departmentId,
      user_input: payload.userInput,
      requested_by: payload.requestedBy,
    });
    return {
      question: String(data?.question || payload.userInput || ""),
      department_id: data?.department_id ? String(data.department_id) : undefined,
      ward_scope: Boolean(data?.ward_scope),
      global_scope: Boolean(data?.global_scope),
      extracted_beds: toStringArray(data?.extracted_beds),
      unresolved_beds: toStringArray(data?.unresolved_beds),
      matched_patients: Array.isArray(data?.matched_patients)
        ? data.matched_patients.map((item: any) => ({
            patient_id: item?.patient_id ? String(item.patient_id) : undefined,
            patient_name: item?.patient_name ? String(item.patient_name) : undefined,
            bed_no: item?.bed_no ? String(item.bed_no) : undefined,
            diagnoses: toStringArray(item?.diagnoses),
            risk_tags: toStringArray(item?.risk_tags),
            pending_tasks: sanitizePendingTasks(item?.pending_tasks),
            requested_bed_no: item?.requested_bed_no ? String(item.requested_bed_no) : undefined,
            resolved_bed_no: item?.resolved_bed_no ? String(item.resolved_bed_no) : undefined,
            bed_no_corrected: Boolean(item?.bed_no_corrected),
            correction_note: item?.correction_note ? String(item.correction_note) : undefined,
          }))
        : [],
    };
  },

  async listAgentQueueTasks(options?: {
    patientId?: string;
    conversationId?: string;
    status?: string;
    limit?: number;
  }): Promise<AgentQueueTask[]> {
    if (isMockMode) {
      return (mockApi as any).listAgentQueueTasks(options);
    }
    return withLocalFallback(
      async () => {
        const { data } = await httpClient.get("/api/ai/queue/tasks", {
          params: {
            patient_id: options?.patientId,
            conversation_id: options?.conversationId,
            status: options?.status,
            limit: options?.limit || 30,
          },
        });
        const rows = Array.isArray(data) ? data : Array.isArray((data as any)?.value) ? (data as any).value : [];
        return rows.map(mapQueueTask);
      },
      () => (mockApi as any).listAgentQueueTasks(options)
    );
  },

  async listAgentRuns(options?: {
    patientId?: string;
    conversationId?: string;
    status?: string;
    workflowType?: string;
    limit?: number;
  }): Promise<AgentRunRecord[]> {
    if (isMockMode) {
      return (mockApi as any).listAgentRuns(options);
    }
    return withLocalFallback(
      async () => {
        const { data } = await httpClient.get("/api/ai/runs", {
          params: {
            patient_id: options?.patientId,
            conversation_id: options?.conversationId,
            status: options?.status,
            workflow_type: options?.workflowType,
            limit: options?.limit || 20,
          },
        });
        const rows = Array.isArray(data) ? data : Array.isArray((data as any)?.value) ? (data as any).value : [];
        return rows.map(mapAgentRunRecord);
      },
      () => (mockApi as any).listAgentRuns(options)
    );
  },

  async getAgentRun(runId: string): Promise<AgentRunRecord> {
    if (isMockMode) {
      return (mockApi as any).getAgentRun(runId);
    }
    try {
      const { data } = await httpClient.get(`/api/ai/runs/${runId}`);
      return mapAgentRunRecord(data);
    } catch (error) {
      throw error;
    }
  },

  async retryAgentRun(runId: string) {
    if (isMockMode) {
      return (mockApi as any).retryAgentRun(runId);
    }
    try {
      const { data } = await httpClient.post(`/api/ai/runs/${runId}/retry`);
      return mapWorkflowOutput(data);
    } catch (error) {
      throw error;
    }
  },

  async enqueueAgentTask(payload: {
    workflowType: string;
    patientId?: string;
    bedNo?: string;
    conversationId?: string;
    departmentId?: string;
    userInput: string;
    missionTitle?: string;
    successCriteria?: string[];
    operatorNotes?: string;
    attachments?: string[];
    requestedBy?: string;
    agentMode?: string;
    executionProfile?: AIExecutionProfile;
    requestedEngine?: string;
    priority?: number;
  }): Promise<AgentQueueTask> {
    if (isMockMode) {
      return (mockApi as any).enqueueAgentTask(payload);
    }
    try {
      const { data } = await httpClient.post("/api/ai/queue/tasks", {
        requested_engine: payload.requestedEngine,
        priority: payload.priority || 100,
        payload: {
          workflow_type: payload.workflowType,
          patient_id: payload.patientId,
          bed_no: payload.bedNo,
          conversation_id: payload.conversationId,
          department_id: payload.departmentId,
          user_input: payload.userInput,
          mission_title: payload.missionTitle,
          success_criteria: payload.successCriteria || [],
          operator_notes: payload.operatorNotes,
          attachments: payload.attachments || [],
          requested_by: payload.requestedBy,
          agent_mode: payload.agentMode,
          execution_profile: payload.executionProfile,
        },
      });
      return mapQueueTask(data);
    } catch (error) {
      throw error;
    }
  },

  async approveAgentQueueTask(payload: {
    taskId: string;
    approvalIds?: string[];
    decidedBy?: string;
    comment?: string;
  }): Promise<AgentQueueTask> {
    if (isMockMode) {
      return (mockApi as any).approveAgentQueueTask(payload);
    }
    try {
      const { data } = await httpClient.post(`/api/ai/queue/tasks/${payload.taskId}/approve`, {
        approval_ids: payload.approvalIds || [],
        decided_by: payload.decidedBy,
        comment: payload.comment,
      });
      return mapQueueTask(data);
    } catch (error) {
      throw error;
    }
  },

  async rejectAgentQueueTask(payload: {
    taskId: string;
    approvalIds?: string[];
    decidedBy?: string;
    comment?: string;
  }): Promise<AgentQueueTask> {
    if (isMockMode) {
      return (mockApi as any).rejectAgentQueueTask(payload);
    }
    try {
      const { data } = await httpClient.post(`/api/ai/queue/tasks/${payload.taskId}/reject`, {
        approval_ids: payload.approvalIds || [],
        decided_by: payload.decidedBy,
        comment: payload.comment,
      });
      return mapQueueTask(data);
    } catch (error) {
      throw error;
    }
  },

  async runAiChat(payload: {
    mode: AIChatMode;
    selectedModel?: string;
    clusterProfile?: string;
    patientId?: string;
    bedNo?: string;
    conversationId?: string;
    departmentId?: string;
    userInput: string;
    missionTitle?: string;
    successCriteria?: string[];
    operatorNotes?: string;
    attachments?: string[];
    requestedBy?: string;
    agentMode?: string;
    executionProfile?: AIExecutionProfile;
  }): Promise<AIChatResponse> {
    if (isMockMode) {
      return mockApi.runAiChat(payload);
    }
    const body = buildAiChatBody(payload);
    const timeout = getAiChatTimeoutMs(payload);
    try {
      const { data } = await httpClient.post("/api/ai/chat", body, { timeout });
      return mapAiChatResponse(data);
    } catch (error) {
      if (!shouldRetryAiChat(error)) {
        if (shouldUseLocalFallback(error)) {
          return mockApi.runAiChat(payload);
        }
        throw error;
      }

      const retryOperatorNotes = [payload.operatorNotes, "自动重试：若链路波动，请优先返回患者定位、核心判断、今日待办和交接摘要。"]
        .filter(Boolean)
        .join("\n");

      try {
        const { data } = await httpClient.post(
          "/api/ai/chat",
          {
            ...body,
            operator_notes: retryOperatorNotes,
          },
          { timeout: timeout + 20_000 }
        );
        return mapAiChatResponse(data);
      } catch (retryError) {
        if (shouldUseLocalFallback(retryError)) {
          return mockApi.runAiChat(payload);
        }
        throw retryError;
      }
    }
  },

  async transcribe(payload: { audioBase64?: string; textHint?: string }) {
    if (isMockMode) {
      return mockApi.transcribe(payload.textHint);
    }
    const { data } = await httpClient.post(
      `${asrBaseURL}/asr/transcribe`,
      {
        text_hint: payload.textHint,
        audio_base64: payload.audioBase64,
      },
      { timeout: 120000 }
    );
    return data;
  },

  async generateHandover(patientId: string): Promise<HandoverResult> {
    if (isMockMode) {
      return mockApi.generateHandover(patientId);
    }
    const { data } = await httpClient.post("/api/handover/generate", {
      patient_id: patientId,
      shift_type: "day",
    });
    return data;
  },

  async getLatestHandover(patientId: string): Promise<HandoverResult> {
    if (isMockMode) {
      return mockApi.generateHandover(patientId);
    }
    const { data } = await httpClient.get(`/api/handover/${patientId}/latest`);
    return data;
  },

  async runRecommendation(
    patientId: string,
    question: string,
    attachments: string[] = [],
    options?: { bedNo?: string; departmentId?: string }
  ): Promise<RecommendationResult> {
    if (isMockMode) {
      return mockApi.runRecommendation(patientId, question);
    }
    const { data } = await httpClient.post("/api/recommendation/run", {
      patient_id: patientId,
      question,
      bed_no: options?.bedNo,
      department_id: options?.departmentId,
      attachments,
    });
    return mapRecommendation(data);
  },

  async getConversationHistory(
    patientId: string,
    limit = 30,
    conversationId?: string
  ): Promise<ConversationHistoryItem[]> {
    if (isMockMode) {
      return mockApi.getConversationHistory(patientId, limit, conversationId);
    }
    const { data } = await httpClient.get("/api/conversation/history", {
      params: { patient_id: patientId, limit, conversation_id: conversationId },
    });
    const rows = Array.isArray(data) ? data : Array.isArray((data as any)?.value) ? (data as any).value : [];
    return mapConversationHistory(rows);
  },

  async getAllHistory(
    patientId: string,
    limit = 80,
    conversationId?: string
  ): Promise<ConversationHistoryItem[]> {
    if (isMockMode) {
      return mockApi.getConversationHistory(patientId, limit, conversationId);
    }
    const { data } = await httpClient.get("/api/history/all", {
      params: { patient_id: patientId, limit, conversation_id: conversationId },
    });
    const rows = Array.isArray(data) ? data : Array.isArray((data as any)?.value) ? (data as any).value : [];
    return mapConversationHistory(rows);
  },

  async listWorkflowHistory(options?: {
    requestedBy?: string;
    conversationId?: string;
    workflowType?: string;
    limit?: number;
  }): Promise<ConversationHistoryItem[]> {
    if (isMockMode) {
      if ((mockApi as any).listWorkflowHistory) {
        return (mockApi as any).listWorkflowHistory(options);
      }
      return mockApi.getConversationHistory(undefined as any, options?.limit || 80, options?.conversationId);
    }
    const { data } = await httpClient.get("/api/workflow/history", {
      params: {
        requested_by: options?.requestedBy,
        conversation_id: options?.conversationId,
        workflow_type: options?.workflowType,
        limit: options?.limit || 80,
      },
    });
    const rows = Array.isArray(data) ? data : Array.isArray((data as any)?.value) ? (data as any).value : [];
    return mapConversationHistory(rows);
  },

  async analyzeMultimodal(
    patientId: string,
    inputRefs: string[],
    question?: string
  ): Promise<MultimodalAnalysisResult> {
    if (isMockMode) {
      return mockApi.analyzeMultimodal(patientId, inputRefs, question);
    }
    const { data } = await httpClient.post("/api/multimodal/analyze", {
      patient_id: patientId,
      input_refs: inputRefs,
      question,
    });
    return data;
  },

  async createDocumentDraft(
    patientId: string,
    spokenText: string,
    options?: {
      documentType?: string;
      templateId?: string;
      templateText?: string;
      templateName?: string;
      requestedBy?: string;
      bedNo?: string;
      patientName?: string;
    }
  ): Promise<DocumentDraft> {
    if (isMockMode) {
      return mockApi.createDocumentDraft(patientId, spokenText, options);
    }
    return withLocalFallback(
      async () => {
        const { data } = await httpClient.post("/api/document/draft", {
          patient_id: patientId,
          document_type: options?.documentType || "nursing_note",
          spoken_text: spokenText,
          template_id: options?.templateId,
          template_text: options?.templateText,
          template_name: options?.templateName,
          requested_by: options?.requestedBy,
          bed_no: options?.bedNo,
          patient_name: options?.patientName,
        });
        return data;
      },
      () => mockApi.createDocumentDraft(patientId, spokenText, options)
    );
  },

  async importDocumentTemplate(payload: {
    name?: string;
    documentType?: string;
    templateText?: string;
    templateBase64?: string;
    fileName?: string;
    mimeType?: string;
    triggerKeywords?: string[];
    sourceRefs?: string[];
  }): Promise<DocumentTemplate> {
    if (isMockMode) {
      return mockApi.importDocumentTemplate(payload);
    }
    const { data } = await httpClient.post("/api/document/template/import", {
      name: payload.name,
      document_type: payload.documentType,
      template_text: payload.templateText,
      template_base64: payload.templateBase64,
      file_name: payload.fileName,
      mime_type: payload.mimeType,
      trigger_keywords: payload.triggerKeywords,
      source_refs: payload.sourceRefs,
    });
    return data;
  },

  async listDocumentTemplates(): Promise<DocumentTemplate[]> {
    if (isMockMode) {
      return mergeDocumentTemplatesWithLocal(await mockApi.listDocumentTemplates());
    }
    return withLocalFallback(
      async () => {
        const { data } = await httpClient.get("/api/document/templates");
        return mergeDocumentTemplatesWithLocal(Array.isArray(data) ? data : []);
      },
      async () => mergeDocumentTemplatesWithLocal(await mockApi.listDocumentTemplates())
    );
  },

  async listStandardForms(): Promise<StandardFormBundle[]> {
    if (isMockMode) {
      return getLocalStandardForms();
    }
    return withLocalFallback(
      async () => {
        const { data } = await httpClient.get("/api/document/standard-forms");
        return mergeStandardFormsWithLocal(Array.isArray(data) ? data : []);
      },
      () => getLocalStandardForms()
    );
  },

  async getStandardForm(documentType: string): Promise<StandardFormBundle> {
    const localForm = getLocalStandardForm(documentType);
    if (localForm) {
      return localForm;
    }
    if (isMockMode) {
      throw new Error("mock_not_implemented");
    }
    return withLocalFallback(
      async () => {
        const { data } = await httpClient.get(`/api/document/standard-forms/${documentType}`);
        return data;
      },
      () => {
        const fallbackForm = getLocalStandardForm(documentType);
        if (!fallbackForm) {
          throw new Error("standard_form_not_found");
        }
        return fallbackForm;
      }
    );
  },

  async listDrafts(patientId: string, requestedBy?: string): Promise<DocumentDraft[]> {
    if (isMockMode) {
      return mockApi.listDrafts(patientId);
    }
    const { data } = await httpClient.get(`/api/document/drafts/${patientId}`, {
      params: {
        requested_by: requestedBy,
      },
    });
    return data;
  },

  async listDocumentHistory(patientId: string, limit = 50, requestedBy?: string): Promise<DocumentDraft[]> {
    if (isMockMode) {
      if (patientId) {
        return mockApi.listDrafts(patientId);
      }
      if ((mockApi as any).getDocumentInbox) {
        return (mockApi as any).getDocumentInbox(requestedBy || "u_nurse_01", { limit });
      }
      return [];
    }
    return withLocalFallback(
      async () => {
        const { data } = await httpClient.get("/api/document/history", {
          params: { patient_id: patientId, requested_by: requestedBy, limit },
        });
        return data;
      },
      async () => {
        if (patientId) {
          return mockApi.listDrafts(patientId);
        }
        if ((mockApi as any).getDocumentInbox) {
          return (mockApi as any).getDocumentInbox(requestedBy || "u_nurse_01", { limit });
        }
        return [];
      }
    );
  },

  async getDocumentInbox(
    requestedBy: string,
    options?: { patientId?: string; limit?: number }
  ): Promise<DocumentDraft[]> {
    if (isMockMode) {
      if ((mockApi as any).getDocumentInbox) {
        return (mockApi as any).getDocumentInbox(requestedBy, options);
      }
      return mockApi.listDrafts(options?.patientId || "pat-001");
    }
    return withLocalFallback(
      async () => {
        const { data } = await httpClient.get(`/api/document/inbox/${requestedBy}`, {
          params: {
            patient_id: options?.patientId,
            limit: options?.limit || 50,
          },
        });
        return data;
      },
      async () => {
        if ((mockApi as any).getDocumentInbox) {
          return (mockApi as any).getDocumentInbox(requestedBy, options);
        }
        return mockApi.listDrafts(options?.patientId || "pat-001");
      }
    );
  },

  async reviewDraft(draftId: string, reviewedBy: string): Promise<DocumentDraft> {
    if (isMockMode) {
      return mockApi.reviewDraft(draftId);
    }
    const { data } = await httpClient.post(`/api/document/${draftId}/review`, { reviewed_by: reviewedBy });
    return data;
  },

  async submitDraft(draftId: string, submittedBy: string): Promise<DocumentDraft> {
    if (isMockMode) {
      return mockApi.submitDraft(draftId);
    }
    const { data } = await httpClient.post(`/api/document/${draftId}/submit`, { submitted_by: submittedBy });
    return data;
  },

  async updateDraft(
    draftId: string,
    payload: string | { draftText: string; editedBy?: string; structuredFields?: Record<string, unknown> },
    editedBy?: string
  ): Promise<DocumentDraft> {
    if (isMockMode) {
      if ((mockApi as any).updateDraft) {
        if (typeof payload === "string") {
          return (mockApi as any).updateDraft(draftId, payload, editedBy);
        }
        return (mockApi as any).updateDraft(draftId, payload.draftText, payload.editedBy || editedBy);
      }
      throw new Error("mock_not_implemented");
    }
    const body =
      typeof payload === "string"
        ? {
            draft_text: payload,
            edited_by: editedBy,
            structured_fields: undefined,
          }
        : {
            draft_text: payload.draftText,
            edited_by: payload.editedBy,
            structured_fields: payload.structuredFields,
          };
    const { data } = await httpClient.post(`/api/document/${draftId}/edit`, {
      draft_text: body.draft_text,
      edited_by: body.edited_by,
      structured_fields: body.structured_fields,
    });
    return data;
  },

  async editDraft(
    draftId: string,
    payload: string | { draftText: string; editedBy?: string; structuredFields?: Record<string, unknown> },
    editedBy?: string
  ): Promise<DocumentDraft> {
    if (isMockMode) {
      if ((mockApi as any).editDraft) {
        if (typeof payload === "string") {
          return (mockApi as any).editDraft(draftId, payload, editedBy);
        }
        return (mockApi as any).editDraft(draftId, payload.draftText, payload.editedBy || editedBy);
      }
      throw new Error("mock_not_implemented");
    }
    const body =
      typeof payload === "string"
        ? {
            draft_text: payload,
            edited_by: editedBy,
            structured_fields: undefined,
          }
        : {
            draft_text: payload.draftText,
            edited_by: payload.editedBy,
            structured_fields: payload.structuredFields,
          };
    const { data } = await httpClient.post(`/api/document/${draftId}/edit`, {
      draft_text: body.draft_text,
      edited_by: body.edited_by,
      structured_fields: body.structured_fields,
    });
    return data;
  },

  async createThread(patientId: string, title: string, createdBy: string) {
    if (isMockMode) {
      return mockApi.createThread(patientId, title, createdBy);
    }
    const { data } = await httpClient.post("/api/collab/thread", {
      patient_id: patientId,
      title,
      thread_type: "discussion",
      created_by: createdBy,
    });
    return data;
  },

  async sendMessage(threadId: string, content: string, senderId: string) {
    if (isMockMode) {
      return mockApi.sendMessage(threadId, content, senderId);
    }
    const { data } = await httpClient.post("/api/collab/message", {
      thread_id: threadId,
      content,
      sender_id: senderId,
    });
    return data;
  },

  async getThread(threadId: string): Promise<CollaborationThreadDetail> {
    if (isMockMode) {
      return mockApi.getThread(threadId);
    }
    const { data } = await httpClient.get(`/api/collab/thread/${threadId}`);
    return data;
  },

  async getCollabHistory(patientId: string, limit = 50): Promise<CollaborationThreadHistoryItem[]> {
    if (isMockMode) {
      return mockApi.getCollabHistory(patientId, limit);
    }
    const { data } = await httpClient.get("/api/collab/history", {
      params: { patient_id: patientId, limit },
    });
    return mapThreadHistory(data);
  },

  async searchCollabAccounts(query: string, excludeUserId?: string): Promise<CollabAccount[]> {
    if (isMockMode) {
      if ((mockApi as any).searchCollabAccounts) {
        return (mockApi as any).searchCollabAccounts(query, excludeUserId);
      }
      return [];
    }
    return withLocalFallback(
      async () => {
        const { data } = await httpClient.get("/api/collab/accounts", {
          params: { query, exclude_user_id: excludeUserId },
        });
        return Array.isArray(data) ? data.map(mapAccount) : [];
      },
      async () => {
        if ((mockApi as any).searchCollabAccounts) {
          return (mockApi as any).searchCollabAccounts(query, excludeUserId);
        }
        return [];
      }
    );
  },

  async getCollabContacts(userId: string): Promise<CollabContactList> {
    if (isMockMode) {
      if ((mockApi as any).getCollabContacts) {
        return (mockApi as any).getCollabContacts(userId);
      }
      return { user_id: userId, contacts: [] };
    }
    return withLocalFallback(
      async () => {
        const { data } = await httpClient.get(`/api/collab/contacts/${userId}`);
        return {
          user_id: String(data?.user_id || userId),
          contacts: Array.isArray(data?.contacts) ? data.contacts.map(mapAccount) : [],
        };
      },
      async () => {
        if ((mockApi as any).getCollabContacts) {
          return (mockApi as any).getCollabContacts(userId);
        }
        return { user_id: userId, contacts: [] };
      }
    );
  },

  async addCollabContact(userId: string, account: string): Promise<CollabAccount> {
    if (isMockMode) {
      if ((mockApi as any).addCollabContact) {
        return (mockApi as any).addCollabContact(userId, account);
      }
      throw new Error("mock_not_implemented");
    }
    return withLocalFallback(
      async () => {
        const { data } = await httpClient.post("/api/collab/contacts/add", {
          user_id: userId,
          account,
        });
        return mapAccount(data);
      },
      async () => {
        if ((mockApi as any).addCollabContact) {
          return (mockApi as any).addCollabContact(userId, account);
        }
        throw new Error("mock_not_implemented");
      }
    );
  },

  async listDirectSessions(userId: string, limit = 100): Promise<DirectSession[]> {
    if (isMockMode) {
      if ((mockApi as any).listDirectSessions) {
        return (mockApi as any).listDirectSessions(userId, limit);
      }
      return [];
    }
    return withLocalFallback(
      async () => {
        const { data } = await httpClient.get(`/api/collab/direct/sessions/${userId}`, {
          params: { limit },
        });
        return Array.isArray(data) ? data.map(mapDirectSession) : [];
      },
      async () => {
        if ((mockApi as any).listDirectSessions) {
          return (mockApi as any).listDirectSessions(userId, limit);
        }
        return [];
      }
    );
  },

  async openDirectSession(payload: {
    userId: string;
    contactUserId: string;
    patientId?: string;
  }): Promise<DirectSession> {
    if (isMockMode) {
      if ((mockApi as any).openDirectSession) {
        return (mockApi as any).openDirectSession(payload);
      }
      throw new Error("mock_not_implemented");
    }
    return withLocalFallback(
      async () => {
        const { data } = await httpClient.post("/api/collab/direct/open", {
          user_id: payload.userId,
          contact_user_id: payload.contactUserId,
          patient_id: payload.patientId,
        });
        return mapDirectSession(data);
      },
      async () => {
        if ((mockApi as any).openDirectSession) {
          return (mockApi as any).openDirectSession(payload);
        }
        throw new Error("mock_not_implemented");
      }
    );
  },

  async sendDirectMessage(payload: {
    sessionId: string;
    senderId: string;
    content: string;
    messageType?: string;
    attachmentRefs?: string[];
  }): Promise<void> {
    if (isMockMode) {
      if ((mockApi as any).sendDirectMessage) {
        await (mockApi as any).sendDirectMessage(payload);
      }
      return;
    }
    await withLocalFallback(
      async () => {
        await httpClient.post("/api/collab/direct/message", {
          session_id: payload.sessionId,
          sender_id: payload.senderId,
          content: payload.content,
          message_type: payload.messageType || "text",
          attachment_refs: payload.attachmentRefs || [],
        });
      },
      async () => {
        if ((mockApi as any).sendDirectMessage) {
          await (mockApi as any).sendDirectMessage(payload);
          return;
        }
        throw new Error("mock_not_implemented");
      }
    );
  },

  async getDirectSessionDetail(sessionId: string, userId: string): Promise<DirectSessionDetail> {
    if (isMockMode) {
      if ((mockApi as any).getDirectSessionDetail) {
        return (mockApi as any).getDirectSessionDetail(sessionId, userId);
      }
      throw new Error("mock_not_implemented");
    }
    return withLocalFallback(
      async () => {
        const { data } = await httpClient.get(`/api/collab/direct/session/${sessionId}`, {
          params: { user_id: userId },
        });
        return {
          session: mapDirectSession(data?.session),
          messages: Array.isArray(data?.messages)
            ? data.messages.map((item: any) => ({
                id: String(item?.id || ""),
                thread_id: String(item?.thread_id || ""),
                sender_id: item?.sender_id ? String(item.sender_id) : undefined,
                message_type: String(item?.message_type || "text"),
                content: String(item?.content || ""),
                attachment_refs: Array.isArray(item?.attachment_refs)
                  ? item.attachment_refs.map((x: any) => String(x))
                  : [],
                ai_generated: Boolean(item?.ai_generated),
                created_at: String(item?.created_at || ""),
              }))
            : [],
        };
      },
      async () => {
        if ((mockApi as any).getDirectSessionDetail) {
          return (mockApi as any).getDirectSessionDetail(sessionId, userId);
        }
        throw new Error("mock_not_implemented");
      }
    );
  },

  async runAssistantDigest(payload: {
    userId: string;
    patientId: string;
    note?: string;
  }): Promise<AssistantDigest> {
    if (isMockMode) {
      if ((mockApi as any).runAssistantDigest) {
        return (mockApi as any).runAssistantDigest(payload);
      }
      return {
        summary: "未启用真实服务，暂无整理结果。",
        tasks: [],
        suggestions: [],
        generated_message: "",
      };
    }
    const { data } = await httpClient.post("/api/collab/assistant/digest", {
      user_id: payload.userId,
      patient_id: payload.patientId,
      note: payload.note,
    });
    return {
      summary: String(data?.summary || ""),
      tasks: Array.isArray(data?.tasks) ? data.tasks.map((x: any) => String(x)) : [],
      suggestions: Array.isArray(data?.suggestions) ? data.suggestions.map((x: any) => String(x)) : [],
      generated_message: String(data?.generated_message || ""),
    };
  },

  async runVoiceWorkflow(userInput: string, patientId?: string) {
    if (isMockMode) {
      return mockApi.runVoiceWorkflow(userInput);
    }
    const body: any = {
      workflow_type: "voice_inquiry",
      user_input: userInput,
    };
    if (patientId) {
      body.patient_id = patientId;
    }
    const { data } = await httpClient.post("/api/workflow/run", body);
    return data;
  },

  async ttsSpeak(text: string) {
    if (isMockMode) {
      return { audio_base64: "TU9DS19BVURJT19EQVRB", provider: "mock" };
    }
    const { data } = await httpClient.post("/api/tts/speak", { text, voice: "default" });
    return data;
  },
};
