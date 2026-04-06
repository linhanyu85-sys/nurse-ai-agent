import type { AIChatMessage, AIChatResponse } from "../types";
import { formatAiText } from "./text";

function cleanAssistantSummary(value: string) {
  return formatAiText(value)
    .split("\n")
    .map((line) => line.trim())
    .filter(
      (line) =>
        line &&
        !/^系统已经先帮你完成一轮梳理/.test(line) &&
        !/^已参考历史记忆[:：]/.test(line) &&
        !/^会话摘要[:：]/.test(line) &&
        !/^已记住[:：]/.test(line) &&
        !/^待继续[:：]/.test(line)
    )
    .join("\n")
    .trim();
}

function buildAssistantResponseText(payload: { text?: string; response?: AIChatResponse }) {
  const response = payload.response;
  const summary = cleanAssistantSummary(response?.summary || payload.text || "");
  const findings = Array.isArray(response?.findings)
    ? response.findings.map((item) => formatAiText(item).trim()).filter(Boolean)
    : [];
  const recommendations = Array.isArray(response?.recommendations)
    ? response.recommendations
        .slice()
        .sort((a, b) => a.priority - b.priority)
        .map((item) => formatAiText(item.title).trim())
        .filter(Boolean)
    : [];
  const nextActions = Array.isArray(response?.next_actions)
    ? response.next_actions.map((item) => formatAiText(item).trim()).filter(Boolean)
    : [];

  const parts: string[] = [];
  if (summary) {
    parts.push(summary);
  }
  if (findings.length) {
    parts.push("", "观察重点：", ...findings.slice(0, 4).map((item) => `• ${item}`));
  }
  if (recommendations.length) {
    parts.push("", "建议动作：", ...recommendations.slice(0, 4).map((item, index) => `${index + 1}. ${item}`));
  }
  if (nextActions.length) {
    parts.push("", "后续提醒：", ...nextActions.slice(0, 3).map((item) => `• ${item}`));
  }
  return parts.join("\n").trim();
}

export function buildAssistantMessageText(message: Pick<AIChatMessage, "text" | "response">) {
  return buildAssistantResponseText(message);
}

export function buildAssistantPreviewText(message?: AIChatMessage | null, maxLines = 3) {
  if (!message) {
    return "";
  }

  const sourceText =
    message.role === "assistant" ? buildAssistantMessageText(message) : formatAiText(String(message.text || ""));

  return sourceText
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .slice(0, maxLines)
    .join(" ");
}
