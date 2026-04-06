from __future__ import annotations

import json
import re
import time
from typing import Any

import httpx

from app.core.config import settings


SYS_MSG = (
    "你是临床护理 AI 助手。"
    "请输出简洁、可执行、以中文为主的结构化结论。"
    "尽量少用技术术语，必须出现专业词时请顺手解释成护士容易理解的话。"
)

_PROBE_TTL_SEC = 12.0
_PROBE_STALE_SEC = 90.0
_probe_cache: dict[str, Any] = {
    "ts": 0.0,
    "reachable": False,
    "models": [],
}


def _probe_cache_payload(*, stale: bool = False) -> dict[str, Any]:
    return {
        "enabled": True,
        "reachable": bool(_probe_cache.get("reachable")),
        "models": list(_probe_cache.get("models") or []),
        "stale": stale,
    }


def _remember_probe(models: list[str]) -> None:
    _probe_cache["ts"] = time.monotonic()
    _probe_cache["reachable"] = True
    _probe_cache["models"] = models


async def _do_chat(
    *,
    base: str,
    mdl: str,
    txt: str,
    system_msg: str | None = None,
    key: str = "",
    tm: int = 30,
) -> str | None:
    url = base.rstrip("/") + "/chat/completions"
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"

    request_body: dict[str, Any] = {
        "model": mdl,
        "messages": [
            {"role": "system", "content": system_msg or SYS_MSG},
            {"role": "user", "content": txt},
        ],
        "temperature": 0.2,
    }

    async with httpx.AsyncClient(timeout=tm, trust_env=False) as client:
        try:
            response = await client.post(url, headers=headers, json=request_body)
            response.raise_for_status()
            try:
                body = json.loads(response.content.decode("utf-8"))
            except Exception:
                body = response.json()
            choices = body.get("choices", [{}])
            first = choices[0] if choices else {}
            message = first.get("message", {})
            content = str(message.get("content", "")).strip()
            return content or None
        except Exception:
            return None


def _get_json(text: str | None) -> dict[str, Any] | None:
    raw = str(text or "").strip()
    if not raw:
        return None

    candidates: list[str] = [raw]
    fenced = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", raw, flags=re.DOTALL | re.IGNORECASE)
    candidates.extend(fenced)

    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        candidates.append(raw[start : end + 1])

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except Exception:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _uniq(*items: str | None) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for item in items:
        normalized = str(item or "").strip()
        if not normalized:
            continue
        token = normalized.lower()
        if token in seen:
            continue
        seen.add(token)
        output.append(normalized)
    return output


def _merge_model_candidates(preferred: list[str], online: list[str]) -> list[str]:
    if not online:
        return preferred

    matched = [model for model in preferred if model.lower() in {item.lower() for item in online}]
    if matched:
        extras = [model for model in online if model.lower() not in {item.lower() for item in matched}]
        return _uniq(*matched, *extras[:2])

    return _uniq(*online[:2], *preferred)


async def local_refine(prompt: str) -> str | None:
    if not settings.local_llm_enabled:
        return None

    preferred = _uniq(settings.local_llm_model_primary, settings.local_llm_model_fallback)
    status = await probe_local_models()
    candidates = _merge_model_candidates(preferred, list(status.get("models") or []))
    if not candidates:
        status = await probe_local_models(force_refresh=True)
        candidates = _merge_model_candidates(preferred, list(status.get("models") or []))
    if not candidates:
        return None

    total_timeout = max(12, int(settings.local_llm_timeout_sec))
    per_timeout = max(6, int(total_timeout / max(1, len(candidates))))
    for model in candidates:
        result = await _do_chat(
            base=settings.local_llm_base_url,
            mdl=model,
            txt=prompt,
            system_msg=SYS_MSG,
            key=settings.local_llm_api_key,
            tm=per_timeout,
        )
        if result:
            return result
    return None


async def local_refine_with_model(prompt: str, model: str, system_msg: str | None = None) -> str | None:
    if not settings.local_llm_enabled:
        return None

    requested_model = str(model or "").strip()
    if not requested_model:
        return None

    status = await probe_local_models()
    online = list(status.get("models") or [])
    candidates = _merge_model_candidates([requested_model], online)
    if not candidates:
        status = await probe_local_models(force_refresh=True)
        candidates = _merge_model_candidates([requested_model], list(status.get("models") or []))
    for candidate in candidates:
        result = await _do_chat(
            base=settings.local_llm_base_url,
            mdl=candidate,
            txt=prompt,
            system_msg=system_msg or SYS_MSG,
            key=settings.local_llm_api_key,
            tm=settings.local_llm_timeout_sec,
        )
        if result:
            return result
    return None


async def local_structured_json(
    prompt: str,
    *,
    model: str = "",
    timeout_sec: int | None = None,
) -> dict[str, Any] | None:
    if not settings.local_llm_enabled:
        return None

    preferred = _uniq(
        model,
        settings.local_llm_model_planner,
        settings.local_llm_model_reasoning,
        settings.local_llm_model_primary,
        settings.local_llm_model_fallback,
    )
    status = await probe_local_models()
    candidates = _merge_model_candidates(preferred, list(status.get("models") or []))
    if not candidates:
        status = await probe_local_models(force_refresh=True)
        candidates = _merge_model_candidates(preferred, list(status.get("models") or []))
    if not candidates:
        return None

    timeout = timeout_sec or settings.agent_planner_timeout_sec
    for candidate in candidates:
        raw = await _do_chat(
            base=settings.local_llm_base_url,
            mdl=candidate,
            txt=prompt,
            system_msg=SYS_MSG,
            key=settings.local_llm_api_key,
            tm=timeout,
        )
        parsed = _get_json(raw)
        if parsed is not None:
            return parsed
    return None


async def probe_local_models(force_refresh: bool = False) -> dict[str, Any]:
    if not settings.local_llm_enabled:
        return {"enabled": False, "reachable": False, "models": []}

    now = time.monotonic()
    if not force_refresh and (now - float(_probe_cache.get("ts") or 0.0)) <= _PROBE_TTL_SEC:
        return _probe_cache_payload()

    url = settings.local_llm_base_url.rstrip("/") + "/models"
    headers: dict[str, str] = {}
    if settings.local_llm_api_key:
        headers["Authorization"] = f"Bearer {settings.local_llm_api_key}"

    last_error: Exception | None = None
    for _ in range(2):
        try:
            async with httpx.AsyncClient(timeout=6, trust_env=False) as client:
                response = await client.get(url, headers=headers)
                response.raise_for_status()
                body = response.json()
            models: list[str] = []
            rows = body.get("data", []) if isinstance(body, dict) else []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                model_id = str(row.get("id") or row.get("model") or "").strip()
                if model_id:
                    models.append(model_id)
            _remember_probe(models)
            return _probe_cache_payload()
        except Exception as exc:
            last_error = exc
            continue

    if _probe_cache.get("reachable") and (now - float(_probe_cache.get("ts") or 0.0)) <= _PROBE_STALE_SEC:
        return _probe_cache_payload(stale=True)

    _probe_cache["reachable"] = False
    _probe_cache["models"] = []
    _probe_cache["ts"] = now
    return {"enabled": True, "reachable": False, "models": [], "error": str(last_error or "")}


async def bailian_refine(prompt: str) -> str:
    if settings.mock_mode and not settings.llm_force_enable:
        return f"[本地演示模式] {prompt[:220]}"

    local_result = await local_refine(prompt)
    if local_result:
        return local_result

    if settings.local_only_mode:
        return (
            "本地回答服务当前不可用，系统已禁止云端回退。"
            "请先启动本地中文模型服务（默认端口 9100），再重新尝试。"
        )

    if settings.bailian_api_key:
        cloud_result = await _do_chat(
            base=settings.bailian_base_url,
            mdl=settings.bailian_model_default,
            txt=prompt,
            system_msg=SYS_MSG,
            key=settings.bailian_api_key,
            tm=18,
        )
        if cloud_result:
            return cloud_result

    if not settings.bailian_api_key:
        return f"已收到问题：{prompt[:120]}。当前云端模型未配置，建议先启动本地中文模型服务。"
    return f"已收到问题：{prompt[:120]}。当前模型调用失败，请人工复核后执行。"
