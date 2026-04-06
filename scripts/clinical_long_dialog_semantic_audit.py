from __future__ import annotations

import json
import os
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = ROOT_DIR / "logs" / "clinical_long_dialog_regression_20.json"
REPORT_PATH = Path(os.environ.get("AI_AUDIT_REPORT") or DEFAULT_REPORT)
TECH_HINTS = ("如需多模态", "切换 MedGemma", "切换 AI Agent 集群", "如需复杂推理")


def load_report(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def main() -> int:
    report = load_report(REPORT_PATH)
    failures: list[str] = []

    for result in report.get("results", []):
        name = str(result.get("name") or "")
        text = str(result.get("merged_text") or "")
        lines = [line.strip() for line in text.splitlines() if line.strip()]

        if any(marker in text for marker in TECH_HINTS):
            failures.append(f"{name}: 出现技术切换提示，不适合作为临床回答。")

        if "双护士" in name:
            if "护士A" not in text or "护士B" not in text:
                failures.append(f"{name}: 缺少明确的双护士分工。")

        for line in lines:
            if ("SpO2=89%" in line or "SpO289%" in line) and "风险分层：低危" in line:
                failures.append(f"{name}: 低氧场景仍被写成低危，风险排序不严谨。")
                break

        for line in lines:
            if ("收缩压=88" in line or "收缩压88" in line or "收缩压=89" in line or "收缩压89" in line) and "风险分层：低危" in line:
                failures.append(f"{name}: 低血压场景仍被写成低危，风险排序不严谨。")
                break

    summary = {
        "report": str(REPORT_PATH),
        "checked": len(report.get("results", [])),
        "failed": len(failures),
        "failures": failures,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
