from __future__ import annotations

import json
import time
import urllib.request
import uuid


API_URL = "http://127.0.0.1:8000/api/ai/chat"


def main() -> None:
    payload = {
        "user_input": "请以病区晨间巡检为起点，安排一个完整 AI Agent 工作流：先排优先级，再生成交班和文书草稿，再指出哪些内容需要护士人工确认，最后形成提交前复核清单。",
        "mode": "agent_cluster",
        "execution_profile": "full_loop",
        "cluster_profile": "nursing_default_cluster",
        "requested_by": "probe_cn_autonomous",
        "conversation_id": f"probe-cn-autonomous-{uuid.uuid4().hex[:8]}",
        "department_id": "dep-card-01",
    }
    request = urllib.request.Request(
        API_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=180) as response:
        body = json.loads(response.read().decode("utf-8"))
    elapsed = round(time.perf_counter() - started, 3)
    preview = {
        "elapsed_sec": elapsed,
        "workflow_type": body.get("workflow_type"),
        "summary": body.get("summary"),
        "findings": body.get("findings"),
        "recommendations": body.get("recommendations"),
        "artifacts": body.get("artifacts"),
        "steps": body.get("steps"),
        "context_hit": body.get("context_hit"),
    }
    print(json.dumps(preview, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
