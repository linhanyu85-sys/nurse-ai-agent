from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


API_URL = os.environ.get("AI_NURSING_API_URL", "http://127.0.0.1:8000/api/ai/chat")
REQUEST_TIMEOUT_SEC = int(os.environ.get("AI_NURSING_TIMEOUT_SEC", "180"))
REQUEST_RETRIES = int(os.environ.get("AI_NURSING_RETRIES", "2"))
ROOT_DIR = Path(__file__).resolve().parents[1]
GENERIC_MARKERS = (
    "未命中患者上下文",
    "请补充床号",
    "建议先补充床号",
    "文书草稿生成需要患者上下文",
    "请告诉我更具体的需求",
    "无法直接给出回答",
    "请提供更具体的问题",
    "本地模型暂时不可用",
    "还未定位到具体患者或病区上下文",
)


@dataclass
class RegressionCase:
    name: str
    category: str
    user_input: str
    mode: str = "agent_cluster"
    execution_profile: str | None = "agent"
    cluster_profile: str | None = "nursing_default_cluster"
    selected_model: str | None = None
    expect_workflows: tuple[str, ...] = ()
    expected_keywords: tuple[str, ...] = ()
    forbid_keywords: tuple[str, ...] = ()
    expect_artifact_kinds: tuple[str, ...] = ()
    forbid_generic_prompt: bool = True
    min_answer_length: int = 120
    min_prompt_length: int = 0
    max_elapsed_sec: float | None = None
    require_context_hit: bool | None = None


def merged_text(response: dict[str, Any]) -> str:
    parts: list[str] = [str(response.get("summary") or "")]
    parts.extend(str(item) for item in response.get("findings") or [])
    for item in response.get("recommendations") or []:
        if isinstance(item, dict):
            parts.append(str(item.get("title") or ""))
        else:
            parts.append(str(item))
    for artifact in response.get("artifacts") or []:
        if isinstance(artifact, dict):
            parts.append(str(artifact.get("title") or ""))
            parts.append(str(artifact.get("summary") or ""))
    return "\n".join(part for part in parts if part).strip()


def artifact_kinds(response: dict[str, Any]) -> set[str]:
    kinds: set[str] = set()
    for artifact in response.get("artifacts") or []:
        if isinstance(artifact, dict):
            kind = str(artifact.get("kind") or "").strip()
            if kind:
                kinds.add(kind)
    return kinds


def context_hit(response: dict[str, Any]) -> bool:
    if response.get("patient_id") or response.get("bed_no"):
        return True
    for step in response.get("steps") or []:
        if not isinstance(step, dict):
            continue
        if str(step.get("agent") or "") == "Patient Context Agent" and str(step.get("status") or "") == "done":
            return True
    return False


def call_api(
    case: RegressionCase,
    index: int,
    suite_id: str,
    retries: int | None = None,
) -> tuple[dict[str, Any] | None, float, str | None]:
    payload = {
        "user_input": case.user_input,
        "mode": case.mode,
        "execution_profile": case.execution_profile,
        "cluster_profile": case.cluster_profile,
        "selected_model": case.selected_model,
        "requested_by": f"{suite_id}_{index:02d}",
        "conversation_id": f"{suite_id}-{index:02d}-{uuid.uuid4().hex[:8]}",
        "department_id": "dep-card-01",
    }
    payload = {key: value for key, value in payload.items() if value is not None}
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(API_URL, data=data, headers={"Content-Type": "application/json"})

    last_error: str | None = None
    elapsed = 0.0
    max_retries = REQUEST_RETRIES if retries is None else retries
    for attempt in range(max_retries + 1):
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SEC) as response:
                body = response.read().decode("utf-8")
            return json.loads(body), time.perf_counter() - started, None
        except urllib.error.HTTPError as exc:
            elapsed = time.perf_counter() - started
            last_error = f"HTTP {exc.code}"
            if exc.code not in {502, 503, 504} or attempt >= max_retries:
                break
            time.sleep(1.5 * (attempt + 1))
        except Exception as exc:  # noqa: BLE001
            elapsed = time.perf_counter() - started
            last_error = str(exc)
            if attempt >= max_retries:
                break
            time.sleep(1.5 * (attempt + 1))
    return None, elapsed, last_error


def check_case(case: RegressionCase, response: dict[str, Any] | None, elapsed: float, error: str | None) -> dict[str, Any]:
    result = {
        "name": case.name,
        "category": case.category,
        "elapsed_sec": round(elapsed, 3),
        "prompt_length": len(case.user_input),
        "passed": False,
        "error": error,
        "reasons": [],
        "workflow_type": None,
        "summary_preview": None,
        "merged_text": None,
        "context_hit": None,
        "artifact_kinds": [],
        "response_snapshot": None,
    }
    if len(case.user_input) < case.min_prompt_length:
        result["reasons"].append(f"提示词长度不足：{len(case.user_input)}")

    if error or response is None:
        result["reasons"].append("接口调用失败")
        return result

    workflow = str(response.get("workflow_type") or "")
    text = merged_text(response)
    current_context_hit = context_hit(response)
    result["workflow_type"] = workflow
    result["summary_preview"] = str(response.get("summary") or "")[:240]
    result["merged_text"] = text
    result["context_hit"] = current_context_hit

    if case.expect_workflows and workflow not in case.expect_workflows:
        result["reasons"].append(f"工作流不匹配：{workflow}")
    if len(text) < case.min_answer_length:
        result["reasons"].append(f"回答过短：{len(text)}")
    if case.max_elapsed_sec is not None and elapsed > case.max_elapsed_sec:
        result["reasons"].append(f"响应过慢：{round(elapsed, 2)}s")
    if case.forbid_generic_prompt and any(marker in text for marker in GENERIC_MARKERS):
        result["reasons"].append("出现泛化追问或要求补床号")
    for keyword in case.expected_keywords:
        if keyword not in text:
            result["reasons"].append(f"缺少关键词：{keyword}")
    for keyword in case.forbid_keywords:
        if keyword in text:
            result["reasons"].append(f"命中禁用关键词：{keyword}")

    kinds = artifact_kinds(response)
    result["artifact_kinds"] = sorted(kinds)
    for kind in case.expect_artifact_kinds:
        if kind not in kinds:
            result["reasons"].append(f"缺少产物：{kind}")

    if case.require_context_hit is True and not current_context_hit:
        result["reasons"].append("未命中患者或病区上下文")
    if case.require_context_hit is False and current_context_hit:
        result["reasons"].append("不应命中患者上下文")

    result["response_snapshot"] = {
        "summary": response.get("summary"),
        "findings": response.get("findings"),
        "recommendations": response.get("recommendations"),
        "next_actions": response.get("next_actions"),
        "artifacts": response.get("artifacts"),
        "patient_id": response.get("patient_id"),
        "bed_no": response.get("bed_no"),
    }
    result["passed"] = not result["reasons"]
    return result


def run_suite(*, suite_name: str, suite_id: str, report_filename: str, cases: list[RegressionCase]) -> int:
    print(f"开始回归：{suite_name}，共 {len(cases)} 条")
    results: list[dict[str, Any]] = []

    for index, case in enumerate(cases, start=1):
        response, elapsed, error = call_api(case, index, suite_id)
        checked = check_case(case, response, elapsed, error)
        results.append(checked)
        status = "PASS" if checked["passed"] else "FAIL"
        print(f"[{status}] {index:02d}. {case.category} / {case.name} ({checked['elapsed_sec']}s)")
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
        "api_url": API_URL,
        "suite_name": suite_name,
        "suite_id": suite_id,
        "total": len(results),
        "passed": passed,
        "failed": failed,
        "by_category": by_category,
        "cases": [asdict(case) for case in cases],
        "results": results,
    }
    report_path = ROOT_DIR / "logs" / report_filename
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("")
    print(json.dumps({"suite": suite_name, "total": len(results), "passed": passed, "failed": failed, "report": str(report_path)}, ensure_ascii=False))
    return 0 if failed == 0 else 1
