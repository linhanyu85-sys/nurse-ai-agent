import type { BedOverview, PatientContext } from "../types";
import { decodeEscapedText } from "./text";

type RiskTone = "danger" | "warning" | "success" | "info";
type RiskSource = "structured" | "inferred" | "missing";

export type ClinicalRiskBadge = {
  label: string;
  tone: RiskTone;
  score: number | null;
  sortKey: number;
  reason: string;
  shortReason: string;
  source: RiskSource;
  sourceLabel: string;
  warning?: string;
  canUseHeatmap: boolean;
};

const LEVEL_RANKS: Record<string, number> = {
  危急: 4,
  高危: 3,
  中危: 2,
  低危: 1,
  待分层: 0,
};

const NORMALIZED_LEVELS: Array<{ aliases: string[]; label: string }> = [
  { aliases: ["危急", "紧急", "critical", "urgent"], label: "危急" },
  { aliases: ["高危", "高风险", "high"], label: "高危" },
  { aliases: ["中危", "中风险", "medium", "moderate"], label: "中危" },
  { aliases: ["低危", "低风险", "low"], label: "低危" },
];

function normalizeText(raw?: string | null) {
  return decodeEscapedText(raw).trim().replace(/\s+/g, "").toLowerCase();
}

function normalizeRiskLabel(raw?: string | null): string {
  const text = normalizeText(raw);
  if (!text) {
    return "待分层";
  }
  const matched = NORMALIZED_LEVELS.find((item) => item.aliases.includes(text));
  return matched?.label || decodeEscapedText(raw).trim() || "待分层";
}

function mapTone(label: string, source: RiskSource): RiskTone {
  if (source !== "structured") {
    return source === "missing" ? "info" : "warning";
  }
  if (label === "危急" || label === "高危") {
    return "danger";
  }
  if (label === "中危") {
    return "warning";
  }
  if (label === "低危") {
    return "success";
  }
  return "info";
}

function buildReason(input: Partial<BedOverview & PatientContext>) {
  const explicitReason = decodeEscapedText(input.risk_reason).trim();
  if (explicitReason) {
    return explicitReason;
  }

  const riskTags = Array.isArray(input.risk_tags)
    ? input.risk_tags.map((item) => decodeEscapedText(item).trim()).filter(Boolean)
    : [];
  if (riskTags.length) {
    return riskTags.join("、");
  }

  const abnormalSignals = Array.isArray(input.latest_observations)
    ? input.latest_observations
        .filter((item) => {
          const flag = String(item?.abnormal_flag || "").trim().toLowerCase();
          return flag && flag !== "normal" && flag !== "ok";
        })
        .map((item) => `${decodeEscapedText(item.name).trim() || "异常指标"} ${decodeEscapedText(item.value).trim()}`.trim())
        .filter(Boolean)
    : [];

  if (abnormalSignals.length) {
    return abnormalSignals.join("；");
  }

  return "";
}

function shortenReason(reason: string) {
  if (!reason) {
    return "等待结构化风险分层";
  }
  return reason.length > 22 ? `${reason.slice(0, 22)}...` : reason;
}

export function buildClinicalRiskBadge(input: Partial<BedOverview & PatientContext>): ClinicalRiskBadge {
  const label = normalizeRiskLabel(input.risk_level);
  const numericScore =
    typeof input.risk_score === "number" && Number.isFinite(input.risk_score) ? input.risk_score : null;
  const reason = buildReason(input);

  let source: RiskSource = "missing";
  if (label !== "待分层") {
    source = "structured";
  } else if (reason) {
    source = "inferred";
  }

  const warning =
    source === "inferred"
      ? "仅收到风险线索，未收到结构化风险分层，已从热力图排除。"
      : source === "missing"
      ? "缺少风险分层与异常依据，需护士确认后再展示。"
      : undefined;

  const sourceLabel = source === "structured" ? "" : source === "inferred" ? "待护士核对" : "数据缺失";

  return {
    label: source === "structured" ? label : "待分层",
    tone: mapTone(label, source),
    score: numericScore,
    sortKey:
      source === "structured"
        ? numericScore ?? LEVEL_RANKS[label] * 100
        : 0,
    reason: reason || warning || "等待病区接口返回结构化风险分层。",
    shortReason: shortenReason(reason || warning || ""),
    source,
    sourceLabel,
    warning,
    canUseHeatmap: source === "structured",
  };
}
