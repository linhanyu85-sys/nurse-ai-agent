import type { BedOverview, PatientContext } from "../types";
import { decodeEscapedText } from "./text";

type NursingLevelTone = {
  label: string;
  shortLabel: string;
  backgroundColor: string;
  borderColor: string;
  textColor: string;
  source: "explicit" | "fallback" | "missing";
};

const LEVEL_STYLES: Record<string, Omit<NursingLevelTone, "source">> = {
  特级护理: {
    label: "特级护理",
    shortLabel: "特护",
    backgroundColor: "#fff1f1",
    borderColor: "#e35d5b",
    textColor: "#b42318",
  },
  一级护理: {
    label: "一级护理",
    shortLabel: "一级",
    backgroundColor: "#fff2f7",
    borderColor: "#ef9fbb",
    textColor: "#c33868",
  },
  二级护理: {
    label: "二级护理",
    shortLabel: "二级",
    backgroundColor: "#fff8d9",
    borderColor: "#6f98f2",
    textColor: "#275db3",
  },
  三级护理: {
    label: "三级护理",
    shortLabel: "三级",
    backgroundColor: "#edf8ef",
    borderColor: "#64b27b",
    textColor: "#1d6b3d",
  },
  待核对: {
    label: "待核对",
    shortLabel: "待核",
    backgroundColor: "#eef6ff",
    borderColor: "#c8d9ee",
    textColor: "#2556a8",
  },
};

const LEVEL_ALIASES: Array<{ aliases: string[]; label: keyof typeof LEVEL_STYLES }> = [
  { aliases: ["特级护理", "特护", "special"], label: "特级护理" },
  { aliases: ["一级护理", "1级护理", "一级", "level1"], label: "一级护理" },
  { aliases: ["二级护理", "2级护理", "二级", "level2"], label: "二级护理" },
  { aliases: ["三级护理", "3级护理", "三级", "level3"], label: "三级护理" },
];

function normalizeText(value: unknown) {
  return decodeEscapedText(value).trim().replace(/\s+/g, "").toLowerCase();
}

function matchExplicitLevel(input: Partial<BedOverview & PatientContext>): keyof typeof LEVEL_STYLES | "" {
  const candidates = [
    input.nursing_level,
    ...(Array.isArray(input.risk_tags) ? input.risk_tags : []),
    ...(Array.isArray(input.pending_tasks) ? input.pending_tasks : []),
    input.latest_document_sync,
  ];
  for (const candidate of candidates) {
    const normalized = normalizeText(candidate);
    if (!normalized) {
      continue;
    }
    const matched = LEVEL_ALIASES.find((item) => item.aliases.some((alias) => normalizeText(alias) === normalized || normalized.includes(normalizeText(alias))));
    if (matched) {
      return matched.label;
    }
  }
  return "";
}

function fallbackLevelFromRisk(input: Partial<BedOverview & PatientContext>): keyof typeof LEVEL_STYLES {
  const normalized = normalizeText(input.risk_level);
  if (normalized.includes("危急") || normalized.includes("critical") || normalized.includes("urgent")) {
    return "特级护理";
  }
  if (normalized.includes("高危") || normalized.includes("high")) {
    return "一级护理";
  }
  if (normalized.includes("中危") || normalized.includes("medium") || normalized.includes("moderate")) {
    return "二级护理";
  }
  if (normalized.includes("低危") || normalized.includes("low")) {
    return "三级护理";
  }
  return "待核对";
}

export function buildNursingLevelTone(input: Partial<BedOverview & PatientContext>): NursingLevelTone {
  const explicit = matchExplicitLevel(input);
  if (explicit) {
    return {
      ...LEVEL_STYLES[explicit],
      source: "explicit",
    };
  }
  const fallback = fallbackLevelFromRisk(input);
  return {
    ...LEVEL_STYLES[fallback],
    source: fallback === "待核对" ? "missing" : "fallback",
  };
}
