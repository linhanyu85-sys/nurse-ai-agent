from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path

import clinical_regression_common as common
from clinical_long_dialog_regression_50 import build_cases


ROOT_DIR = Path(__file__).resolve().parents[1]


def main() -> int:
    common.REQUEST_RETRIES = int(os.environ.get("AI_NURSING_RETRIES", "0"))
    common.REQUEST_TIMEOUT_SEC = int(os.environ.get("AI_NURSING_TIMEOUT_SEC", "120"))

    suite_name = "50组千字级临床AI Agent长对话回归"
    suite_id = "clinical_long50"
    report_path = ROOT_DIR / "logs" / "clinical_long_dialog_regression_50.json"
    cases = build_cases()
    parallelism = max(1, int(os.environ.get("AI_NURSING_PARALLELISM", "3")))

    print(f"开始并行回归：{suite_name}，共 {len(cases)} 组，parallelism={parallelism}")
    started_at = time.perf_counter()
    results: list[dict] = [None] * len(cases)  # type: ignore[list-item]

    def run_one(index: int, case):
        response, elapsed, error = common.call_api(case, index + 1, suite_id)
        checked = common.check_case(case, response, elapsed, error)
        return index, checked

    with ThreadPoolExecutor(max_workers=parallelism) as executor:
        futures = [executor.submit(run_one, index, case) for index, case in enumerate(cases)]
        for future in as_completed(futures):
            index, checked = future.result()
            results[index] = checked
            status = "PASS" if checked["passed"] else "FAIL"
            print(f"[{status}] {index + 1:02d}. {checked['category']} / {checked['name']} ({checked['elapsed_sec']}s)")
            if checked["reasons"]:
                for reason in checked["reasons"]:
                    print(f"  - {reason}")

    passed = sum(1 for item in results if item["passed"])
    failed = len(results) - passed
    by_category: dict[str, dict[str, int]] = {}
    for item in results:
        bucket = by_category.setdefault(item["category"], {"passed": 0, "failed": 0})
        bucket["passed" if item["passed"] else "failed"] += 1

    report = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "elapsed_sec": round(time.perf_counter() - started_at, 3),
        "api_url": common.API_URL,
        "suite_name": suite_name,
        "suite_id": suite_id,
        "parallelism": parallelism,
        "total": len(results),
        "passed": passed,
        "failed": failed,
        "by_category": by_category,
        "cases": [asdict(case) for case in cases],
        "results": results,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("")
    print(json.dumps({"suite": suite_name, "total": len(results), "passed": passed, "failed": failed, "report": str(report_path)}, ensure_ascii=False))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
