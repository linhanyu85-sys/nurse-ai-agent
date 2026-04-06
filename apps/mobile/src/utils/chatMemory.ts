import type { AIChatMessage, AIChatResponse } from "../types";
import { compactText, formatBedLabel, normalizePersonName } from "./displayValue";
import { formatAiText } from "./text";

export type SessionMemoryPatch = {
  memorySummary?: string;
  memoryFacts?: string[];
  memoryTodos?: string[];
  lastPatientId?: string;
  lastPatientName?: string;
  lastBedNo?: string;
};

type MemoryContext = {
  bedNo?: string;
  patientName?: string;
};

const MEMORY_NOISE_PATTERNS = [
  /^系统已经先帮你完成一轮梳理/,
  /^已参考历史记忆[:：]?/,
  /^会话摘要[:：]?/,
  /^已记住[:：]?/,
  /^待继续[:：]?/,
  /^示例[:：]?/,
  /^如果后续/,
  /^如果需要/,
  /^继续直接追问/,
  /当前按系统能力与临床落地解释处理/,
  /当前未绑定具体病例/,
  /风险热力图/,
  /今日待办/,
  /交接班摘要看板/,
  /页面模块/,
  /workflow/i,
  /ai agent/i,
  /test patient/i,
];

function uniq(items: Array<string | undefined | null>, max = 6) {
  const seen = new Set<string>();
  const out: string[] = [];

  items.forEach((item) => {
    const value = compactText(item).trim();
    if (!value || seen.has(value)) {
      return;
    }
    seen.add(value);
    out.push(value);
  });

  return out.slice(0, max);
}

function latestAssistantResponse(messages: AIChatMessage[]) {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (message.role === "assistant" && message.response) {
      return message.response;
    }
  }
  return undefined;
}

function latestAssistantText(messages: AIChatMessage[]) {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (message.role === "assistant" && message.text) {
      return cleanMemorySummaryText(message.text);
    }
  }
  return "";
}

function recommendationTitles(response?: AIChatResponse) {
  return Array.isArray(response?.recommendations) ? response.recommendations.map((item) => item.title) : [];
}

function normalizeMemoryLine(value: string | undefined | null) {
  return compactText(formatAiText(value), 160).trim();
}

function normalizeBedToken(value: string | undefined) {
  return String(value || "")
    .trim()
    .replace(/[床号\s]/g, "");
}

function extractBedTokens(value: string) {
  const matches = value.match(/([A-Za-z]?\d{1,3})\s*床/g) || [];
  return matches.map((item) => normalizeBedToken(item)).filter(Boolean);
}

function isMemoryNoise(line: string) {
  return MEMORY_NOISE_PATTERNS.some((pattern) => pattern.test(line));
}

function matchesMemoryContext(line: string, context: MemoryContext) {
  const currentBed = normalizeBedToken(context.bedNo);
  if (currentBed) {
    const bedTokens = extractBedTokens(line);
    if (bedTokens.length && !bedTokens.includes(currentBed)) {
      return false;
    }
  }

  const patientName = normalizePersonName(context.patientName);
  if (patientName && /test patient/i.test(line) && !line.includes(patientName)) {
    return false;
  }

  return true;
}

export function cleanMemorySummaryText(value: string | undefined) {
  return formatAiText(value)
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line && !isMemoryNoise(line))
    .join(" ")
    .trim();
}

export function cleanMemoryList(items: Array<string | undefined | null>, context: MemoryContext = {}, max = 6) {
  return uniq(
    items
      .map((item) => normalizeMemoryLine(item))
      .filter((line) => line && !isMemoryNoise(line) && matchesMemoryContext(line, context)),
    max
  );
}

export function buildSessionMemory(messages: AIChatMessage[], response?: AIChatResponse): SessionMemoryPatch {
  const latestResponse = response || latestAssistantResponse(messages);
  const patientName = normalizePersonName(latestResponse?.patient_name);
  const bedNo = latestResponse?.bed_no || "";
  const memoryContext = { bedNo, patientName };

  const summary =
    compactText(cleanMemorySummaryText(latestResponse?.summary), 120) ||
    compactText(latestAssistantText(messages), 120) ||
    compactText(cleanMemorySummaryText(latestResponse?.memory?.conversation_summary), 120);

  const memoryFacts = cleanMemoryList(
    [
      patientName && bedNo ? `${formatBedLabel(bedNo)} 路 ${patientName}` : patientName || (bedNo ? formatBedLabel(bedNo) : ""),
      ...(latestResponse?.memory?.patient_facts || []),
      ...(latestResponse?.findings || []),
    ],
    memoryContext
  );

  const memoryTodos = cleanMemoryList(
    [
      ...(latestResponse?.memory?.unresolved_tasks || []),
      ...(latestResponse?.next_actions || []),
      ...recommendationTitles(latestResponse),
    ],
    memoryContext,
    5
  );

  return {
    memorySummary: summary || undefined,
    memoryFacts,
    memoryTodos,
    lastPatientId: latestResponse?.patient_id || undefined,
    lastPatientName: patientName || undefined,
    lastBedNo: bedNo || undefined,
  };
}

export function buildAiOperatorNotes(memory: SessionMemoryPatch | undefined, extraNotes?: string[]) {
  const sections: string[] = [];

  if (memory?.memorySummary) {
    sections.push(`会话摘要：${memory.memorySummary}`);
  }

  if (memory?.memoryFacts?.length) {
    sections.push(`已确认事实：${memory.memoryFacts.slice(0, 4).join("；")}`);
  }

  if (memory?.memoryTodos?.length) {
    sections.push(`待继续事项：${memory.memoryTodos.slice(0, 4).join("；")}`);
  }

  const extras = (extraNotes || []).map((item) => compactText(item, 120)).filter(Boolean);
  if (extras.length) {
    sections.push(...extras);
  }

  return sections.join("\n").trim() || undefined;
}
