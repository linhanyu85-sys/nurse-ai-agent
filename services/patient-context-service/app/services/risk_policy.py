from __future__ import annotations

from typing import Any


RISK_KEYWORDS: dict[str, float] = {
    "低氧": 4.5,
    "呼吸": 3.0,
    "再出血": 4.0,
    "出血": 3.5,
    "意识": 4.0,
    "卒中": 3.5,
    "低血压": 3.5,
    "休克": 4.5,
    "感染": 2.8,
    "高钾": 3.2,
    "血糖": 2.5,
    "疼痛": 2.0,
    "容量": 2.2,
    "液体": 2.0,
    "病情波动": 3.2,
}

TASK_KEYWORDS: dict[str, float] = {
    "立即": 2.6,
    "立刻": 2.6,
    "通知医生": 3.0,
    "上报": 2.8,
    "双人核对": 2.0,
    "每小时": 1.6,
    "持续": 1.4,
    "复测": 1.0,
    "监测": 0.8,
}

OBS_FLAG_SCORE: dict[str, float] = {
    "critical": 6.0,
    "high": 3.4,
    "low": 1.6,
    "abnormal": 2.4,
    "warning": 2.4,
}


def evaluate_clinical_risk(
    *,
    risk_tags: list[str] | None = None,
    pending_tasks: list[str] | None = None,
    latest_observations: list[dict[str, Any]] | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    if str(status or "").strip().lower() == "vacant":
        return {"risk_level": "低危", "risk_score": 0.0, "risk_reason": "当前床位为空。"}

    score = 0.0
    reasons: list[str] = []

    for tag in risk_tags or []:
        text = str(tag or "").strip()
        if not text:
            continue
        weight = 1.8
        for keyword, keyword_score in RISK_KEYWORDS.items():
            if keyword in text:
                weight = max(weight, keyword_score)
        score += weight
        reasons.append(text)

    for task in pending_tasks or []:
        text = str(task or "").strip()
        if not text:
            continue
        task_score = 0.6
        for keyword, keyword_score in TASK_KEYWORDS.items():
            if keyword in text:
                task_score = max(task_score, keyword_score)
        score += task_score

    for item in latest_observations or []:
        if not isinstance(item, dict):
            continue
        flag = str(item.get("abnormal_flag") or "").strip().lower()
        if not flag:
            continue
        score += OBS_FLAG_SCORE.get(flag, 0.0)
        if flag in {"critical", "high"}:
            name = str(item.get("name") or "关键指标").strip() or "关键指标"
            value = str(item.get("value") or "").strip()
            reasons.append(f"{name}{value}".strip())

    if score >= 12:
        level = "危急"
    elif score >= 8:
        level = "高危"
    elif score >= 4:
        level = "中危"
    else:
        level = "低危"

    reason = "、".join(dict.fromkeys(reasons)) if reasons else "暂无突出风险信号。"
    return {"risk_level": level, "risk_score": round(score, 1), "risk_reason": reason}
