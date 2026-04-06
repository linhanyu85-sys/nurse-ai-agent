import type {
  DocumentDraft,
  DocumentStructuredFields,
  DraftEditableBlock,
  DraftSectionMeta,
  Patient,
  PatientContext,
  StandardFormBundle,
  StandardFormMeta,
} from "../types";
import { getDocumentTypeLabel } from "./displayText";
import { normalizeBedNo as normalizeDisplayBedNo, normalizePersonName } from "./displayValue";
import { decodeEscapedText } from "./text";

type ArchiveMetaInput = {
  bedNo?: string;
  patientName?: string;
  patientId?: string;
};

type DraftHydrationOptions = {
  standardForm: StandardFormBundle;
  patient?: Patient | null;
  context?: PatientContext | null;
};

export type DraftArchiveIdentity = {
  groupKey: string;
  title: string;
  subtitle: string;
  bedNo?: string;
  patientName?: string;
  patientId?: string;
  patientIdHint?: string;
};

function toStructuredFields(value: unknown): DocumentStructuredFields {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return {};
  }
  return value as DocumentStructuredFields;
}

function safeText(value: unknown, fallback = ""): string {
  const text = decodeEscapedText(value).trim();
  return text || fallback;
}

function safeListText(value: unknown, fallback = ""): string {
  if (Array.isArray(value)) {
    const joined = value.map((item) => safeText(item)).filter(Boolean).join("、");
    return joined || fallback;
  }
  return safeText(value, fallback);
}

function firstPresent(...values: unknown[]): string {
  for (const value of values) {
    const text = safeListText(value);
    if (!isMissingValue(text)) {
      return text;
    }
  }
  return "";
}

function looksLikeIdentifier(value: string): boolean {
  const text = safeText(value);
  if (!text) {
    return false;
  }
  if (/^[0-9a-f]{8}-[0-9a-f-]{27,}$/i.test(text)) {
    return true;
  }
  if (/^(pat|patient|mrn|ip)[-_:/]?[a-z0-9-]+$/i.test(text)) {
    return true;
  }
  if (/^[0-9-]{12,}$/.test(text)) {
    return true;
  }
  return false;
}

function normalizeDisplayName(value: string): string {
  const text = normalizePersonName(value, "");
  if (!text || isMissingValue(text) || looksLikeIdentifier(text)) {
    return "";
  }
  return text;
}

function normalizeBedNo(value: string): string {
  const text = normalizeDisplayBedNo(value);
  if (!text) {
    return "";
  }
  if (isMissingValue(text)) {
    return "";
  }
  const cleaned = text.replace(/床位|床号|床/g, "").trim();
  const compactMatch = cleaned.match(/[A-Za-z]?\d{1,3}/);
  const compactBedNo = compactMatch?.[0];
  if (compactBedNo) {
    return safeText(compactBedNo, cleaned);
  }
  const match = text.match(/([A-Za-z]?\d{1,3}|[一二两三四五六七八九十百零]+床?)/);
  return safeText(match?.[1], text.replace(/床位|号床|床/g, "").trim());
}

function isMissingValue(value: string): boolean {
  const normalized = value.trim();
  if (!normalized) {
    return true;
  }
  const lower = normalized.toLowerCase();
  if (containsPlaceholderToken(normalized) || isMostlyGarbled(normalized)) {
    return true;
  }
  if (/^[?？]+$/.test(normalized)) {
    return true;
  }
  if (/^(?:待补|待完善|待评估|待签名|待处理)/.test(normalized)) {
    return true;
  }
  return [
    "-",
    "无",
    "暂无",
    "未知",
    "未填写",
    "未提供",
    "none",
    "null",
    "n/a",
    "na",
    "undefined",
  ].includes(lower);
}

function containsPlaceholderToken(value: string): boolean {
  const normalized = value.trim();
  if (!normalized) {
    return false;
  }
  if (/\{\{\s*[^{}]+\s*\}\}/.test(normalized)) {
    return true;
  }
  if (/^\{[^{}]+\}$/.test(normalized)) {
    return true;
  }
  return false;
}

function isMostlyGarbled(value: string): boolean {
  const visible = value.replace(/\s+/g, "");
  if (visible.length < 6) {
    return false;
  }
  const garbledCount = (visible.match(/[?？\uFFFD]/g) || []).length;
  return garbledCount * 10 >= visible.length * 4;
}

function getFieldValue(structuredFields: DocumentStructuredFields, keys: string[]): string {
  const keySet = new Set(keys.map((item) => item.toLowerCase()));
  const blocks = Array.isArray(structuredFields.editable_blocks) ? structuredFields.editable_blocks : [];
  for (const block of blocks) {
    const blockKey = safeText(block?.key).toLowerCase();
    if (blockKey && keySet.has(blockKey)) {
      return safeText(block?.value);
    }
  }
  for (const key of keys) {
    const direct = structuredFields[key];
    if (typeof direct === "string" && direct.trim()) {
      return direct.trim();
    }
  }
  return "";
}

function shortPatientHint(patientId?: string): string {
  const raw = safeText(patientId);
  if (!raw) {
    return "";
  }
  if (raw.length <= 12) {
    return raw;
  }
  return `${raw.slice(0, 8)}...${raw.slice(-4)}`;
}

function parseBedNoFromText(text: string): string {
  const match = text.match(/床号[:：]?\s*([A-Za-z0-9一二两三四五六七八九十百零]+)|([0-9]{1,3})床/);
  return normalizeBedNo(safeText(match?.[1] || match?.[2]));
}

function parsePatientNameFromText(text: string): string {
  const match = text.match(/(?:患者姓名|姓名)[:：]?\s*([^\s，。；:：]+)/);
  return normalizeDisplayName(safeText(match?.[1]));
}

function parsePatientIdFromText(text: string): string {
  const match = text.match(/(?:病案号|住院号|ID)[:：]?\s*([A-Za-z0-9-]+)/);
  return safeText(match?.[1]);
}

function formatIsoDate(value: string): string {
  const raw = safeText(value);
  if (!raw) {
    return "";
  }
  const parsed = new Date(raw);
  if (Number.isNaN(parsed.getTime())) {
    return "";
  }
  return `${parsed.getFullYear()}-${String(parsed.getMonth() + 1).padStart(2, "0")}-${String(parsed.getDate()).padStart(2, "0")}`;
}

function formatIsoDateTime(value: string): string {
  const raw = safeText(value);
  if (!raw) {
    return "";
  }
  const parsed = new Date(raw);
  if (Number.isNaN(parsed.getTime())) {
    return "";
  }
  return `${formatIsoDate(raw)} ${String(parsed.getHours()).padStart(2, "0")}:${String(parsed.getMinutes()).padStart(2, "0")}`;
}

function extractText(text: string, patterns: RegExp[]): string {
  for (const pattern of patterns) {
    const match = text.match(pattern);
    const value = safeText(match?.[1]);
    if (value) {
      return value;
    }
  }
  return "";
}

function buildLegacyObservationSummary(context?: PatientContext | null): string {
  const rows = Array.isArray(context?.latest_observations) ? context?.latest_observations || [] : [];
  return rows
    .map((item) => {
      const name = safeText(item?.name);
      const value = safeText(item?.value);
      const abnormal = safeText(item?.abnormal_flag);
      if (!name || !value) {
        return "";
      }
      return abnormal ? `${name}：${value}（${abnormal}）` : `${name}：${value}`;
    })
    .filter(Boolean)
    .join("；");
}

function findObservationValue(context: PatientContext | null | undefined, aliases: string[]): string {
  const rows = Array.isArray(context?.latest_observations) ? context?.latest_observations || [] : [];
  const normalized = aliases.map((item) => item.trim().toLowerCase()).filter(Boolean);
  for (const row of rows) {
    const name = safeText(row?.name).toLowerCase();
    const value = safeText(row?.value);
    if (!name || !value) {
      continue;
    }
    if (normalized.some((alias) => name.includes(alias))) {
      return value;
    }
  }
  return "";
}

function buildStandardFormMeta(bundle: StandardFormBundle): StandardFormMeta {
  return {
    id: bundle.form_id,
    name: bundle.name,
    standard_family: bundle.standard_family,
    description: bundle.description,
    schema_version: bundle.schema_version,
    source_refs: bundle.source_refs,
    sections: bundle.sections,
    field_count: bundle.field_count,
    sheet_columns: bundle.sheet_columns,
    questionnaire: bundle.questionnaire,
  };
}

function buildSectionSummary(blocks: DraftEditableBlock[]): DraftSectionMeta[] {
  const grouped = new Map<string, DraftEditableBlock[]>();
  blocks.forEach((block) => {
    grouped.set(block.section, [...(grouped.get(block.section) || []), block]);
  });
  return Array.from(grouped.entries()).map(([title, items]) => ({
    title,
    field_count: items.length,
    missing_count: items.filter((item) => item.required && isMissingValue(safeText(item.value))).length,
    field_keys: items.map((item) => item.key),
  }));
}

function buildLegacyDraftLookup(
  draft: DocumentDraft,
  patient?: Patient | null,
  context?: PatientContext | null
): Record<string, string> {
  const structuredFields = getStructuredFields(draft);
  const text = safeText(draft.draft_text);
  const diagnosisText = firstPresent(structuredFields.diagnoses, context?.diagnoses, extractText(text, [/(?:涓昏璇婃柇|璇婃柇)[:：]\s*([^\n]+)/]));
  const riskTagsText = firstPresent(structuredFields.risk_tags, context?.risk_tags, extractText(text, [/(?:椋庨櫓鏍囩|閲嶇偣椋庨櫓)[:：]\s*([^\n]+)/]));
  const pendingTasksText = firstPresent(
    structuredFields.pending_tasks,
    context?.pending_tasks,
    extractText(text, [/(?:寰呭鐞嗕换鍔?|涓嬩竴鐝瀵熼噸鐐?)[:：]\s*([^\n]+)/])
  );
  const narrativeText = firstPresent(
    structuredFields.spoken_text,
    structuredFields.special_notes,
    extractText(text, [/(?:鎶ょ悊璁板綍|鎶ょ悊鎺柦涓庢晥鏋?|24灏忔椂鐥呮儏鍙婂鐞?|琛ュ厖璇存槑|澶囨敞)[:：]\s*([^\n]+)/])
  );

  return {
    patient_id: firstPresent(structuredFields.patient_id, draft.patient_id, parsePatientIdFromText(text)),
    patient_name: firstPresent(
      structuredFields.patient_name,
      structuredFields.full_name,
      patient?.full_name,
      context?.patient_name,
      parsePatientNameFromText(text)
    ),
    full_name: firstPresent(
      structuredFields.full_name,
      structuredFields.patient_name,
      patient?.full_name,
      context?.patient_name,
      parsePatientNameFromText(text)
    ),
    gender: firstPresent(structuredFields.gender, patient?.gender, extractText(text, [/(?:鎬у埆)[:：]\s*([^\n\s]+)/])),
    age: firstPresent(structuredFields.age, patient?.age, extractText(text, [/(?:骞撮緞)[:：]\s*([^\n\s]+)/])),
    bed_no: firstPresent(structuredFields.bed_no, context?.bed_no, parseBedNoFromText(text)),
    mrn: firstPresent(structuredFields.mrn, patient?.mrn, extractText(text, [/(?:鐥呮鍙?)[:：]\s*([^\n\s]+)/])),
    inpatient_no: firstPresent(structuredFields.inpatient_no, patient?.inpatient_no, extractText(text, [/(?:浣忛櫌鍙?)[:：]\s*([^\n\s]+)/])),
    chart_date: firstPresent(structuredFields.chart_date, formatIsoDate(draft.updated_at)),
    current_time: firstPresent(structuredFields.current_time, formatIsoDateTime(draft.updated_at)),
    admission_date: firstPresent(structuredFields.admission_date),
    department_name: firstPresent(structuredFields.department_name),
    ward_name: firstPresent(structuredFields.ward_name),
    diagnoses: diagnosisText,
    risk_level: firstPresent(structuredFields.risk_level, context?.risk_level, extractText(text, [/(?:椋庨櫓绛夌骇)[:：]\s*([^\n]+)/])),
    risk_tags: riskTagsText,
    pending_tasks: pendingTasksText,
    observation_summary: firstPresent(
      structuredFields.observation_summary,
      buildLegacyObservationSummary(context),
      extractText(text, [/(?:鐥呮儏瑙傚療|鐗规畩鐥呮儏)[:：]\s*([^\n]+)/])
    ),
    special_notes: narrativeText,
    spoken_text: narrativeText,
    requested_by: firstPresent(
      structuredFields.requested_by,
      extractText(text, [/(?:鎶ゅ＋绛惧悕|璁板綍鎶ゅ＋|璁板綍浜?|璐ｄ换鎶ゅ＋|浜ょ彮浜虹鍚?)[:：]\s*([^\n]+)/])
    ),
    receiver_sign: firstPresent(structuredFields.receiver_sign, extractText(text, [/(?:鎺ョ彮浜虹鍚?)[:：]\s*([^\n]+)/])),
    blood_pressure: firstPresent(structuredFields.blood_pressure, findObservationValue(context, ["bp", "blood pressure"])),
    temperature_value: firstPresent(structuredFields.temperature_value, findObservationValue(context, ["temperature", "temp"])),
    pulse_value: firstPresent(structuredFields.pulse_value, findObservationValue(context, ["pulse"])),
    heart_rate_value: firstPresent(structuredFields.heart_rate_value, findObservationValue(context, ["heart rate", "hr", "pulse"])),
    respiratory_rate: firstPresent(structuredFields.respiratory_rate, findObservationValue(context, ["respiratory", "respiration", "rr"])),
    spo2_value: firstPresent(structuredFields.spo2_value, findObservationValue(context, ["spo2", "oxygen"])),
    blood_glucose_value: firstPresent(structuredFields.blood_glucose_value, findObservationValue(context, ["glucose"])),
    pain_score: firstPresent(structuredFields.pain_score, findObservationValue(context, ["pain"])),
    intake_summary: firstPresent(structuredFields.intake_summary, structuredFields.intake_total),
    output_summary: firstPresent(structuredFields.output_summary, structuredFields.output_total),
    intake_total: firstPresent(structuredFields.intake_total),
    output_total: firstPresent(structuredFields.output_total),
    template_name: firstPresent(structuredFields.template_name),
  };
}

export function getStructuredFields(draft?: DocumentDraft | null): DocumentStructuredFields {
  return toStructuredFields(draft?.structured_fields);
}

export function getEditableBlocks(draft?: DocumentDraft | null): DraftEditableBlock[] {
  const fields = getStructuredFields(draft);
  if (!Array.isArray(fields.editable_blocks)) {
    return [];
  }

  return fields.editable_blocks
    .filter((item): item is DraftEditableBlock => Boolean(item && typeof item === "object"))
    .map((item) => {
      const value = safeText(item.value);
      const resolvedValue = isMissingValue(value) ? "" : value;
      return {
        key: safeText(item.key),
        label: safeText(item.label || item.key),
        section: safeText(item.section, "文书内容"),
        value: resolvedValue,
        required: Boolean(item.required),
        editable: item.editable !== false,
        status: isMissingValue(resolvedValue) ? "missing" : safeText(item.status, "filled"),
        input_type: safeText(item.input_type) || undefined,
        placeholder: safeText(item.placeholder) || undefined,
      };
    });
}

export function getSectionMeta(draft?: DocumentDraft | null): DraftSectionMeta[] {
  const fields = getStructuredFields(draft);
  if (Array.isArray(fields.sections)) {
    return fields.sections
      .filter((item): item is DraftSectionMeta => Boolean(item && typeof item === "object"))
      .map((item) => ({
        key: item.key ? safeText(item.key) : undefined,
        title: safeText(item.title || item.key, "文书内容"),
        field_count: Number(item.field_count || 0),
        missing_count: Number(item.missing_count || 0),
        field_keys: Array.isArray(item.field_keys) ? item.field_keys.map((key) => safeText(key)) : [],
      }));
  }

  const blocks = getEditableBlocks(draft);
  const groupMap = new Map<string, DraftEditableBlock[]>();
  blocks.forEach((block) => {
    groupMap.set(block.section, [...(groupMap.get(block.section) || []), block]);
  });

  return Array.from(groupMap.entries()).map(([title, items]) => ({
    title,
    field_count: items.length,
    missing_count: items.filter((item) => item.required && isMissingValue(item.value)).length,
    field_keys: items.map((item) => item.key),
  }));
}

function getSectionMetaFromBlocks(blocks: DraftEditableBlock[]): DraftSectionMeta[] {
  const groupMap = new Map<string, DraftEditableBlock[]>();
  blocks.forEach((block) => {
    groupMap.set(block.section, [...(groupMap.get(block.section) || []), block]);
  });

  return Array.from(groupMap.entries()).map(([title, items]) => ({
    title,
    field_count: items.length,
    missing_count: items.filter((item) => item.required && isMissingValue(String(item.value || ""))).length,
    field_keys: items.map((item) => item.key),
  }));
}

export function updateEditableBlockValue(
  structuredFields: DocumentStructuredFields | undefined,
  key: string,
  value: string
): DocumentStructuredFields {
  const next = { ...(structuredFields || {}) };
  const blocks = Array.isArray(next.editable_blocks) ? [...next.editable_blocks] : [];
  next.editable_blocks = blocks.map((item) => {
    if (!item || typeof item !== "object") {
      return item;
    }
    if (safeText((item as DraftEditableBlock).key) !== key) {
      return item;
    }
    const trimmed = safeText(value);
    return {
      ...item,
      value,
      status: isMissingValue(trimmed) ? "missing" : "filled",
    };
  }) as DraftEditableBlock[];

  const editableBlocks = next.editable_blocks as DraftEditableBlock[];
  next.missing_fields = editableBlocks
    .filter((item) => item.required && isMissingValue(String(item.value || "")))
    .map((item) => ({
      key: item.key,
      label: item.label,
      section: item.section,
    }));
  next.field_summary = {
    total: editableBlocks.length,
    filled: editableBlocks.filter((item) => !isMissingValue(String(item.value || ""))).length,
    missing: (next.missing_fields || []).length,
  };
  next.sections = getSectionMetaFromBlocks(editableBlocks);
  return next;
}

export function getDraftStandardForm(draft?: DocumentDraft | null): StandardFormMeta | undefined {
  const standardForm = getStructuredFields(draft).standard_form;
  if (!standardForm || typeof standardForm !== "object") {
    return undefined;
  }
  return standardForm;
}

export function getSheetColumns(draft?: DocumentDraft | null) {
  const standardForm = getDraftStandardForm(draft);
  if (!Array.isArray(standardForm?.sheet_columns)) {
    return [];
  }
  return standardForm.sheet_columns.map((item) => ({
    key: safeText(item.key),
    label: safeText(item.label || item.key),
    section: safeText(item.section, "文书内容"),
    required: Boolean(item.required),
    input_type: safeText(item.input_type) || undefined,
  }));
}

export function hydrateDraftForEditing(draft: DocumentDraft, options: DraftHydrationOptions): DocumentDraft {
  const currentBlocks = getEditableBlocks(draft);
  const currentForm = getDraftStandardForm(draft);
  const existing = getStructuredFields(draft);
  const lookup = buildLegacyDraftLookup(draft, options.patient, options.context);
  const resolvedForm = currentForm?.sheet_columns?.length ? currentForm : buildStandardFormMeta(options.standardForm);
  const currentBlockByKey = new Map(currentBlocks.map((item) => [item.key, item]));
  const editableBlocks: DraftEditableBlock[] = (resolvedForm.sheet_columns || []).map((column) => {
    const existingValue = getFieldValue(existing, [column.key]);
    const currentValue = currentBlockByKey.get(column.key)?.value || "";
    const value = firstPresent(currentValue, existingValue, lookup[column.key]);
    return {
      key: safeText(column.key),
      label: safeText(column.label || column.key),
      section: safeText(column.section, "文书内容"),
      value,
      required: Boolean(column.required),
      editable: true,
      status: isMissingValue(value) ? "missing" : "filled",
      input_type: safeText(column.input_type) || undefined,
      placeholder: column.required ? "请填写" : "可补充",
    };
  });

  const missingFields = editableBlocks
    .filter((item) => item.required && isMissingValue(safeText(item.value)))
    .map((item) => ({
      key: item.key,
      label: item.label,
      section: item.section,
    }));

  return {
    ...draft,
    structured_fields: {
      ...existing,
      template_name: firstPresent(existing.template_name, lookup.template_name, resolvedForm.name),
      template_applied: existing.template_applied !== false,
      render_mode: safeText(existing.render_mode, "legacy_hydrated"),
      standardized_format: true,
      editable: true,
      editable_blocks: editableBlocks,
      sections: buildSectionSummary(editableBlocks),
      missing_fields: missingFields,
      field_summary: {
        total: editableBlocks.length,
        filled: editableBlocks.filter((item) => !isMissingValue(safeText(item.value))).length,
        missing: missingFields.length,
      },
      standard_form: resolvedForm,
      draft_outline: safeText(draft.draft_text)
        .split("\n")
        .map((line) => safeText(line))
        .filter(Boolean)
        .slice(0, 20),
    },
  };
}

export function getDraftArchiveIdentity(draft: DocumentDraft, meta?: ArchiveMetaInput): DraftArchiveIdentity {
  const structuredFields = getStructuredFields(draft);
  const text = safeText(draft.draft_text);
  const bedNo = normalizeBedNo(
    safeText(meta?.bedNo) || getFieldValue(structuredFields, ["bed_no", "bedNo", "bed"]) || parseBedNoFromText(text)
  );
  const patientName = normalizeDisplayName(
    safeText(meta?.patientName) ||
      getFieldValue(structuredFields, ["patient_name", "full_name", "name"]) ||
      parsePatientNameFromText(text)
  );
  const patientId = safeText(meta?.patientId) || safeText(draft.patient_id) || getFieldValue(structuredFields, ["patient_id"]);
  const patientIdHint = shortPatientHint(
    getFieldValue(structuredFields, ["mrn", "inpatient_no"]) || parsePatientIdFromText(text) || patientId
  );

  if (!bedNo && !patientName && !patientId) {
    return {
      groupKey: "unresolved",
      title: "待补患者信息",
      subtitle: "请先在编辑器中补全床号或患者姓名，再进行审核与归档。",
      patientId: undefined,
      patientIdHint,
    };
  }

  const title = bedNo ? `${bedNo}床${patientName ? ` · ${patientName}` : ""}` : patientName || "患者档案";
  const subtitle = patientIdHint ? `病历索引：${patientIdHint}` : "点击查看患者档案与文书";
  return {
    groupKey: patientId ? `patient:${patientId}` : bedNo ? `bed:${bedNo}` : `draft:${draft.id}`,
    title,
    subtitle,
    bedNo: bedNo || undefined,
    patientName: patientName || undefined,
    patientId: patientId || undefined,
    patientIdHint: patientIdHint || undefined,
  };
}

export function renderStructuredDraftText(documentType: string, structuredFields: DocumentStructuredFields | undefined): string {
  const blocks = getEditableBlocks({ structured_fields: structuredFields } as DocumentDraft);
  if (!blocks.length) {
    return "";
  }

  const title = getDocumentTypeLabel(documentType);
  const grouped = new Map<string, DraftEditableBlock[]>();
  blocks.forEach((block) => {
    grouped.set(block.section, [...(grouped.get(block.section) || []), block]);
  });

  const lines: string[] = [`【${title}】`];
  grouped.forEach((items, section) => {
    lines.push(`${section}：`);
    items.forEach((item) => {
      lines.push(`- ${item.label}：${String(item.value || "").trim() || "待补充"}`);
    });
  });

  return lines.join("\n").trim();
  const text = lines.join("\n").trim();
  if (text.includes("人工复核后提交")) {
    return text;
  }
  return `${text}\n\n[AI提示] 该草稿需护士人工复核后提交。`;
}

export function formatArchiveHint(draft: DocumentDraft) {
  if (draft.status === "submitted") {
    return "已归档到患者病例";
  }
  if (draft.status === "reviewed") {
    return "已审核，待提交归档";
  }

  const missing = Number(getStructuredFields(draft).field_summary?.missing || 0);
  if (missing > 0) {
    return `草稿待补充，仍有 ${missing} 项待完善`;
  }
  return "草稿待护士确认";
}

const PREVIEW_IGNORED_KEYS = new Set([
  "patient_id",
  "patient_name",
  "full_name",
  "name",
  "bed_no",
  "bed",
  "mrn",
  "inpatient_no",
  "requested_by",
  "supervisor_sign",
  "receiver_sign",
  "chart_date",
  "shift_date",
  "current_time",
  "admission_date",
]);

const PREVIEW_IGNORED_SECTION_TOKENS = ["基本信息", "签名信息", "记录信息"];

const META_LINE_PATTERNS = [
  /^\[[^\]]+\]$/,
  /^患者ID[:：]/,
  /^AI提示[:：]/,
  /^\[AI提示\]/,
  /^文书状态[:：]/,
  /^最新文书[:：]/,
  /^待处理任务[:：]\s*(?:文书状态[:：]|最新文书[:：]).*$/,
];

function isPreviewCandidate(block: DraftEditableBlock) {
  const value = String(block.value || "").trim();
  if (!value || isMissingValue(value) || /^[\s?？\uFFFD.,:;!/_-]+$/.test(value) || !value.replace(/[?？\uFFFD]/g, "").trim()) {
    return false;
  }
  if ((block.label.includes("特殊情况") || block.section.includes("特殊情况")) && /[?？\uFFFD]{4,}/.test(value)) {
    return false;
  }
  if (PREVIEW_IGNORED_KEYS.has(block.key)) {
    return false;
  }
  if (PREVIEW_IGNORED_SECTION_TOKENS.some((token) => block.section.includes(token))) {
    return false;
  }
  return true;
}

function scorePreviewBlock(block: DraftEditableBlock) {
  let score = 0;
  if (block.required) {
    score += 3;
  }
  if (block.input_type === "textarea") {
    score += 2;
  }
  if (block.section.includes("病情") || block.section.includes("护理") || block.section.includes("观察")) {
    score += 2;
  }
  if (block.section.includes("生命体征") || block.section.includes("出入量")) {
    score += 1;
  }
  return score;
}

function sanitizeDraftDisplayLines(text: string) {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => {
      if (!line) {
        return false;
      }
      if (containsPlaceholderToken(line) || isMissingValue(line)) {
        return false;
      }
      if (/^[\s?？\uFFFD.,:;!/_-]+$/.test(line) || !line.replace(/[?？\uFFFD]/g, "").trim()) {
        return false;
      }
      if (/:?\s*\{\{[^{}]+\}\}/.test(line)) {
        return false;
      }
      return !META_LINE_PATTERNS.some((pattern) => pattern.test(line));
    });
}

export function buildDocumentPreviewBlocks(draft?: DocumentDraft | null, max = 6) {
  return getEditableBlocks(draft)
    .filter(isPreviewCandidate)
    .sort((a, b) => scorePreviewBlock(b) - scorePreviewBlock(a) || a.section.localeCompare(b.section))
    .slice(0, max);
}

export function buildDocumentPreviewText(draft?: DocumentDraft | null, maxLines = 6) {
  if (!draft) {
    return "";
  }

  const previewBlocks = buildDocumentPreviewBlocks(draft, maxLines);
  if (previewBlocks.length) {
    return previewBlocks.map((block) => `${block.label}：${block.value}`).join("\n");
  }

  const sanitizedLines = sanitizeDraftDisplayLines(safeText(draft.draft_text));
  return sanitizedLines.slice(0, maxLines).join("\n");
}

export function buildSheetRows(draft?: DocumentDraft | null) {
  const blocks = getEditableBlocks(draft);
  const columns = getSheetColumns(draft);
  const valuesByKey = new Map(blocks.map((item) => [item.key, item]));

  if (!columns.length) {
    return blocks.map((item) => ({
      key: item.key,
      section: item.section,
      label: item.label,
      value: item.value,
      required: item.required,
      status: item.status,
      input_type: item.input_type,
      placeholder: item.placeholder,
    }));
  }

  return columns.map((column) => {
    const matched = valuesByKey.get(column.key);
    return {
      key: column.key,
      section: column.section,
      label: column.label,
      value: matched?.value || "",
      required: column.required,
      status: matched?.status || (isMissingValue(matched?.value || "") ? "missing" : "filled"),
      input_type: matched?.input_type || column.input_type,
      placeholder: matched?.placeholder,
    };
  });
}
