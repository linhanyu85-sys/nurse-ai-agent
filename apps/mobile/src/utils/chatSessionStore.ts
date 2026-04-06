import { Platform } from "react-native";
import * as FileSystem from "expo-file-system";

import type { AIChatMessage, AIChatMode, AIExecutionProfile } from "../types";
import { decodeEscapedText } from "./text";

const STORAGE_KEY = "ai_nursing_chat_sessions_v3";
const FILE_NAME = "ai-nursing-chat-sessions-v3.json";
const MAX_SESSIONS = 30;
const MAX_MESSAGES_PER_SESSION = 80;

export type ChatSessionRecord = {
  id: string;
  title: string;
  conversationId: string;
  mode: AIChatMode;
  selectedModel?: string;
  clusterProfile?: string;
  executionProfile?: AIExecutionProfile;
  createdAt: string;
  updatedAt: string;
  lastPrompt: string;
  lastSummary: string;
  memorySummary?: string;
  memoryFacts?: string[];
  memoryTodos?: string[];
  lastPatientId?: string;
  lastPatientName?: string;
  lastBedNo?: string;
  messages: AIChatMessage[];
};

function storagePath() {
  return `${FileSystem.documentDirectory || FileSystem.cacheDirectory || ""}${FILE_NAME}`;
}

function getWebLocalStorage(): Storage | undefined {
  const g = globalThis as { localStorage?: Storage } | undefined;
  return g?.localStorage;
}

async function readRaw() {
  if (Platform.OS === "web") {
    return getWebLocalStorage()?.getItem(STORAGE_KEY) || "";
  }

  const target = storagePath();
  if (!target) {
    return "";
  }
  try {
    return await FileSystem.readAsStringAsync(target);
  } catch {
    return "";
  }
}

async function writeRaw(value: string) {
  if (Platform.OS === "web") {
    getWebLocalStorage()?.setItem(STORAGE_KEY, value);
    return;
  }

  const target = storagePath();
  if (!target) {
    return;
  }
  await FileSystem.writeAsStringAsync(target, value);
}

function normalizeExecutionProfile(value: string | undefined): AIExecutionProfile | undefined {
  if (value === "observe" || value === "escalate" || value === "document" || value === "full_loop") {
    return value;
  }
  return undefined;
}

function sanitizeMessage(raw: any): AIChatMessage | null {
  if (!raw || typeof raw !== "object") {
    return null;
  }
  const role = raw.role === "assistant" ? "assistant" : raw.role === "user" ? "user" : null;
  const mode = raw.mode === "single_model" ? "single_model" : raw.mode === "agent_cluster" ? "agent_cluster" : null;
  if (!role || !mode) {
    return null;
  }
  return {
    id: String(raw.id || `${role}-${Date.now()}`),
    role,
    mode,
    text: decodeEscapedText(raw.text),
    timestamp: String(raw.timestamp || new Date().toISOString()),
    response: raw.response || undefined,
  };
}

function sanitizeSession(raw: any): ChatSessionRecord | null {
  if (!raw || typeof raw !== "object") {
    return null;
  }
  const mode = raw.mode === "single_model" ? "single_model" : raw.mode === "agent_cluster" ? "agent_cluster" : null;
  if (!mode) {
    return null;
  }
  const messages = Array.isArray(raw.messages) ? raw.messages.map(sanitizeMessage).filter(Boolean) : [];
  return {
    id: String(raw.id || raw.conversationId || `chat-${Date.now()}`),
    title: decodeEscapedText(raw.title || "新对话"),
    conversationId: String(raw.conversationId || raw.id || `chat-${Date.now()}`),
    mode,
    selectedModel: raw.selectedModel ? String(raw.selectedModel) : undefined,
    clusterProfile: raw.clusterProfile ? String(raw.clusterProfile) : undefined,
    executionProfile: normalizeExecutionProfile(raw.executionProfile),
    createdAt: String(raw.createdAt || new Date().toISOString()),
    updatedAt: String(raw.updatedAt || new Date().toISOString()),
    lastPrompt: decodeEscapedText(raw.lastPrompt),
    lastSummary: decodeEscapedText(raw.lastSummary),
    memorySummary: decodeEscapedText(raw.memorySummary),
    memoryFacts: Array.isArray(raw.memoryFacts) ? raw.memoryFacts.map((item: unknown) => decodeEscapedText(item)).filter(Boolean) : undefined,
    memoryTodos: Array.isArray(raw.memoryTodos) ? raw.memoryTodos.map((item: unknown) => decodeEscapedText(item)).filter(Boolean) : undefined,
    lastPatientId: raw.lastPatientId ? String(raw.lastPatientId) : undefined,
    lastPatientName: decodeEscapedText(raw.lastPatientName),
    lastBedNo: decodeEscapedText(raw.lastBedNo),
    messages: messages as AIChatMessage[],
  };
}

export function buildChatSessionTitle(question: string) {
  const trimmed = String(question || "").replace(/\s+/g, " ").trim();
  if (!trimmed) {
    return "新对话";
  }
  return trimmed.length > 18 ? `${trimmed.slice(0, 18)}...` : trimmed;
}

export async function loadChatSessions() {
  const raw = await readRaw();
  if (!raw) {
    return [] as ChatSessionRecord[];
  }
  try {
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) {
      return [] as ChatSessionRecord[];
    }
    return parsed
      .map(sanitizeSession)
      .filter(Boolean)
      .sort((a, b) => String(b!.updatedAt).localeCompare(String(a!.updatedAt))) as ChatSessionRecord[];
  } catch {
    return [] as ChatSessionRecord[];
  }
}

export async function saveChatSessions(sessions: ChatSessionRecord[]) {
  const trimmed = sessions
    .slice()
    .sort((a, b) => String(b.updatedAt).localeCompare(String(a.updatedAt)))
    .slice(0, MAX_SESSIONS)
    .map((session) => ({
      ...session,
      messages: session.messages.slice(-MAX_MESSAGES_PER_SESSION),
    }));
  await writeRaw(JSON.stringify(trimmed));
}

export function upsertChatSession(sessions: ChatSessionRecord[], nextSession: ChatSessionRecord) {
  const rest = sessions.filter((item) => item.id !== nextSession.id);
  return [nextSession, ...rest].slice(0, MAX_SESSIONS);
}
