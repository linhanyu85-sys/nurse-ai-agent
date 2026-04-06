import { decodeEscapedText } from "./text";

function cleanText(value: unknown) {
  return decodeEscapedText(value).replace(/\s+/g, " ").trim();
}

function isPlaceholderToken(value: string) {
  const text = String(value || "").trim();
  if (!text) {
    return true;
  }
  const lowered = text.toLowerCase();
  return (
    /^[-–—_/\\.\s]+$/.test(text) ||
    ["null", "undefined", "n/a", "na", "none", "unknown", "unk"].includes(lowered) ||
    ["未分配", "未分配床位", "未分床", "待分配", "待补", "待补充", "patient_not_found"].includes(text)
  );
}

export function normalizeBedNo(value: unknown) {
  const text = cleanText(value);
  if (!text || isPlaceholderToken(text)) {
    return "";
  }

  const stripped = text.replace(/床位|号床|床/g, "").replace(/[^\w\u4e00-\u9fff-]/g, "").trim();
  if (!stripped || isPlaceholderToken(stripped)) {
    return "";
  }

  const match = stripped.match(/([A-Za-z]?\d{1,3}[A-Za-z]?|[一二两三四五六七八九十百零甲乙丙丁戊己庚辛壬癸]{1,5})/);
  if (match?.[1]) {
    return match[1];
  }

  return stripped;
}

export function formatBedLabel(value: unknown, fallback = "未分配床位") {
  const bedNo = normalizeBedNo(value);
  return bedNo ? `${bedNo}床` : fallback;
}

export function normalizePersonName(value: unknown, fallback = "") {
  const text = cleanText(value);
  if (!text || isPlaceholderToken(text)) {
    return fallback;
  }
  const normalized = text.replace(/[路·]/g, " ").replace(/\s+/g, " ").trim();
  return normalized && !isPlaceholderToken(normalized) ? normalized : fallback;
}

export function compactText(value: unknown, maxLength = 44) {
  const text = cleanText(value);
  if (text.length <= maxLength) {
    return text;
  }
  return `${text.slice(0, maxLength)}...`;
}
