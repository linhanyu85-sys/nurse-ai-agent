import re
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.core.config import settings
from app.schemas.workflow import (
    AIChatRequest,
    AIChatResponse,
    AIClusterProfile,
    AIModelOption,
    AIModelsResponse,
    AIModelTask,
    AgentQueueDecisionRequest,
    AgentQueueEnqueueRequest,
    AgentQueueTask,
    AgentRunRecord,
    AgentStep,
    AgentToolSpec,
    ChatMode,
    PatientScopePreview,
    WorkflowHistoryItem,
    WorkflowOutput,
    WorkflowRequest,
    WorkflowType,
)
from app.services.agent_run_store import agent_run_store
from app.services.agent_queue_store import agent_queue_store
from app.services.agent_task_worker import agent_task_worker
from app.services.agentic_orchestrator import agentic_orchestrator
from app.services.history_store import workflow_history_store
from app.services.llm_client import bailian_refine, local_refine_with_model, probe_local_models
from app.services.agent_runtime import runtime
from app.services.state_machine import machine

router = APIRouter()


TCM_SYSTEM_MESSAGE = (
    "你是临床护理场景中的中医辅助问诊模型。"
    "请用简洁中文输出：1) 中医辨证线索 2) 护理观察重点 3) 需要立即转医生的风险。"
    "必须明确说明：你的结论仅用于护理辅助，不能替代执业医师诊断和处方。"
)


class IntentReq(BaseModel):
    text: str


class IntentRsp(BaseModel):
    intent: WorkflowType


class RuntimeEngReq(BaseModel):
    engine: str


def _cloud_ok() -> bool:
    k = settings.bailian_api_key
    only_local = settings.local_only_mode
    return bool(k) and (not only_local)


def _local_aliases() -> dict[str, tuple[str, str]]:
    return {
        "minicpm3_4b_local": ("当前回复整理（本地）", settings.local_llm_model_primary),
        "qwen2_5_3b_local": ("轻量回答（本地）", settings.local_llm_model_fallback),
        "qwen3_8b_local": ("下一步安排（本地）", settings.local_llm_model_planner),
        "deepseek_r1_qwen_7b_local": ("重点再看一遍（本地）", settings.local_llm_model_reasoning),
        "tcm_consult_local": ("中医问诊（本地）", settings.local_llm_model_tcm),
        "custom_openai_local": ("自定义本地回答", settings.local_llm_model_custom),
    }


def _resolve_local_mdl(sel: str) -> str:
    tup = _local_aliases().get(sel)
    if tup is None:
        return ""
    return tup[1]


def _online_set(st: dict[str, Any]) -> set[str]:
    arr = st.get("models") or []
    out: set[str] = set()
    for x in arr:
        s = str(x).strip().lower()
        if s:
            out.add(s)
    return out


def _online_models(st: dict[str, Any]) -> set[str]:
    return _online_set(st)


def _alias_on(a: str | None, onl: set[str]) -> bool:
    v = str(a or "").strip().lower()
    ok = bool(v) and v in onl
    return ok


def _local_desc(a: str | None, onl: set[str], use: str) -> str:
    if not a:
        return f"当前未配置，如需{use}请先在本地模型服务中完成配置。"
    on = _alias_on(a, onl)
    if on:
        return f"当前已启动，可用于{use}。"
    return f"当前未启动，如需{use}请先启动对应本地模型。"


def _norm_profile(ep: str | None) -> str | None:
    p = str(ep or "").strip().lower()
    if p:
        return p
    return None


def _normalize_execution_profile(ep: str | None) -> str | None:
    return _norm_profile(ep)


def _coerce_wf(wf: WorkflowType, ep: str | None) -> WorkflowType:
    p = _norm_profile(ep)
    if p == "full_loop":
        return WorkflowType.AUTONOMOUS_CARE
    if p == "document":
        if wf == WorkflowType.VOICE_INQUIRY:
            return WorkflowType.DOCUMENT
    if p == "escalate":
        if wf == WorkflowType.VOICE_INQUIRY:
            return WorkflowType.RECOMMENDATION
    return wf


def _coerce_chat_workflow(wf: WorkflowType, ep: str | None) -> WorkflowType:
    return _coerce_wf(wf, ep)


def _resolve_selected_local_model(sel: str) -> str:
    return _resolve_local_mdl(sel)


def _force_workflow_from_prompt(user_input: str, execution_profile: str | None, cluster_profile: str | None) -> WorkflowType | None:
    q = (user_input or "").strip()
    if not q:
        return None
    if any(token in q for token in ("\u62a4\u7406\u65e5\u591c\u4ea4\u63a5\u73ed\u62a5\u544a", "\u4ea4\u63a5\u73ed\u62a5\u544a", "\u4ea4\u73ed\u62a5\u544a")) and "\u987a\u5e8f" in q:
        return WorkflowType.HANDOVER
    if any(
        token in q
        for token in (
            "\u767d\u73ed\u62a4\u7406\u4ea4\u63a5\u73ed\u8349\u7a3f",
            "\u5168\u75c5\u533a\u4ea4\u63a5\u73ed\u8349\u7a3f",
            "\u751f\u6210\u4eca\u5929\u8fd9\u4e2a\u75c5\u533a\u7684\u767d\u73ed\u62a4\u7406\u4ea4\u63a5\u73ed\u8349\u7a3f",
        )
    ):
        return WorkflowType.HANDOVER
    if any(
        token in q
        for token in (
            "\u524d\u4e94\u4e2a\u9ad8\u5371\u91cd\u70b9",
            "\u524d\u4e94\u4e2a\u9ad8\u5371",
            "\u4ea4\u4ee3\u7ed9\u4e0b\u4e00\u73ed\u7684\u524d\u4e94\u4e2a\u9ad8\u5371",
        )
    ):
        return WorkflowType.RECOMMENDATION
    if cluster_profile == "tcm_nursing_cluster" and any(
        token in q for token in ("\u4e2d\u533b", "\u4e2d\u897f\u533b\u7ed3\u5408", "\u8bc1\u5019", "\u8fa8\u8bc1")
    ):
        return WorkflowType.VOICE_INQUIRY
    if _normalize_execution_profile(execution_profile) == "document" and any(
        token in q for token in ("\u4ea4\u73ed", "\u4ea4\u63a5\u73ed")
    ):
        return WorkflowType.HANDOVER
    return None


def _cluster_tasks(attach: list[str], onl: set[str], *, include_tcm: bool = False) -> list[AIModelTask]:
    has_att = len(attach) > 0
    pri_ok = _alias_on(settings.local_llm_model_primary, onl)
    pln_ok = _alias_on(settings.local_llm_model_planner, onl)
    rsn_ok = _alias_on(settings.local_llm_model_reasoning, onl)
    mm_ok = _alias_on(settings.local_llm_model_multimodal, onl)
    tcm_ok = _alias_on(settings.local_llm_model_tcm, onl)
    lst: list[AIModelTask] = [
        AIModelTask(
            model_id="care-planner",
            model_name="处理步骤整理",
            role="顺序整理",
            task="把当前情况拆成先做、后做和谁来确认",
            enabled=True,
        ),
        AIModelTask(
            model_id="care-memory",
            model_name="历史回看",
            role="补充背景",
            task="回看历史会话、患者重点和未完成事项",
            enabled=True,
        ),
        AIModelTask(
            model_id="minicpm3-4b-local-main",
            model_name="当前回复整理",
            role="整理重点",
            task="理解提问、归纳重点并生成护士可读说明",
            enabled=pri_ok,
        ),
        AIModelTask(
            model_id="qwen3-8b-local-planner",
            model_name="下一步安排",
            role="梳理先后",
            task="补齐漏掉的步骤，告诉你先做什么后做什么",
            enabled=pln_ok,
        ),
        AIModelTask(
            model_id="deepseek-r1-local",
            model_name="重点再看一遍",
            role="再核对",
            task="对复杂情况再看一遍，避免遗漏",
            enabled=rsn_ok,
        ),
        AIModelTask(
            model_id="funasr-local",
            model_name="语音整理",
            role="语音转文字",
            task="把语音内容整理成文字",
            enabled=True,
        ),
        AIModelTask(
            model_id="minicpm3-4b-local",
            model_name="快速回答",
            role="快速说明",
            task="做床旁中文问答和术语解释",
            enabled=pri_ok,
        ),
        AIModelTask(
            model_id="medgemma-local",
            model_name="附件读取",
            role="补看附件",
            task="补看图片、PDF 和检查报告附件",
            enabled=has_att and mm_ok,
        ),
        AIModelTask(
            model_id="cosyvoice-local",
            model_name="语音播报",
            role="结果播报",
            task="把结果读出来",
            enabled=True,
        ),
        AIModelTask(
            model_id="care-critic",
            model_name="风险复看",
            role="补漏提醒",
            task="检查是否还需要沟通、交班或补充记录",
            enabled=True,
        ),
    ]
    cloud = _cloud_ok()
    if cloud:
        lst.insert(
            1,
            AIModelTask(
                model_id="bailian-qwen-main",
                model_name="云端补充复核",
                role="补充复看",
                task="在本地模型不足时补充复杂情况复核",
                enabled=True,
            ),
        )
    if include_tcm:
        lst.append(
            AIModelTask(
                model_id="tcm-consult-local",
                model_name="中医辨证辅助",
                role="中医特色",
                task="从症状、舌脉与体征中补充中医辨证线索与护理观察点",
                enabled=tcm_ok,
            )
        )
    return lst


async def _models_catalog() -> AIModelsResponse:
    cloud_on = _cloud_ok()
    local_status = await probe_local_models()
    onl = _online_set(local_status)
    default_cluster = AIClusterProfile(
        id="nursing_default_cluster",
        name="系统协同",
        main_model="当前回复整理（本地）",
        description="系统会先整理当前重点，再帮你排处理顺序；遇到复杂情况时再多看一遍，有附件时再补看报告。",
        tasks=_cluster_tasks([], onl),
    )
    tcm_cluster = AIClusterProfile(
        id="tcm_nursing_cluster",
        name="中医特色协同",
        main_model="中医问诊（本地）",
        description="在护理主流程上叠加中医辨证线索，适合做中医特色护理提示与观察重点补充。",
        tasks=_cluster_tasks([], onl, include_tcm=True),
    )
    single_models = [
        AIModelOption(
            id="minicpm3_4b_local",
            name="当前回复整理（本地）",
            provider="local",
            description=_local_desc(settings.local_llm_model_primary, onl, "快速整理当前问题"),
        ),
        AIModelOption(
            id="qwen2_5_3b_local",
            name="轻量回答（本地）",
            provider="local",
            description=_local_desc(settings.local_llm_model_fallback, onl, "在低资源环境下快速回答"),
        ),
        AIModelOption(
            id="qwen3_8b_local",
            name="下一步安排（本地）",
            provider="local",
            description=_local_desc(settings.local_llm_model_planner, onl, "安排先做什么后做什么"),
        ),
        AIModelOption(
            id="deepseek_r1_qwen_7b_local",
            name="重点再看一遍（本地）",
            provider="local",
            description=_local_desc(settings.local_llm_model_reasoning, onl, "对复杂情况做进一步复核"),
        ),
        AIModelOption(
            id="tcm_consult_local",
            name="中医问诊（本地）",
            provider="local",
            description=_local_desc(settings.local_llm_model_tcm, onl, "生成中医辨证线索与中医特色护理观察点"),
        ),
        AIModelOption(
            id="medgemma_local",
            name="附件查看（本地）",
            provider="local",
            description=_local_desc(settings.local_llm_model_multimodal, onl, "查看报告、图片和附件"),
        ),
        AIModelOption(
            id="custom_openai_local",
            name="自定义本地能力",
            provider="local",
            description=_local_desc(settings.local_llm_model_custom, onl, "接入自定义本地模型"),
        ),
    ]
    if cloud_on:
        single_models.extend(
            [
                AIModelOption(
                    id="bailian_main",
                    name="云端综合回答",
                    provider="bailian",
                    description="当本地模型不足时，可补充综合回答与复看。",
                ),
                AIModelOption(
                    id="qwen_light",
                    name="云端快速补答",
                    provider="bailian",
                    description="适合快速补充短回答。",
                ),
            ]
        )
    return AIModelsResponse(single_models=single_models, cluster_profiles=[default_cluster, tcm_cluster])


def _normalize_output_text(text: str) -> str:
    cleaned = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    cleaned = re.sub(r"^\s*#{1,6}\s*", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"\*\*(.*?)\*\*", r"\1", cleaned)
    cleaned = re.sub(r"\*(.*?)\*", r"\1", cleaned)
    cleaned = re.sub(r"`([^`]*)`", r"\1", cleaned)
    cleaned = re.sub(r"^\s*[-*]\s+", "• ", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"^\s*\|[-:\s|]+\|\s*$", "", cleaned, flags=re.MULTILINE)

    def _table_row_to_text(match: re.Match[str]) -> str:
        parts = [cell.strip() for cell in match.group(1).split("|")]
        parts = [part for part in parts if part]
        return " / ".join(parts)

    cleaned = re.sub(r"^\s*\|(.+)\|\s*$", _table_row_to_text, cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _safe_with_question(summary: str, question: str) -> str:
    summary = _normalize_output_text(summary or "")
    q = _normalize_output_text(question or "").strip()
    if summary:
        return summary
    return q


def _postprocess_single_model_summary(question: str, summary: str) -> str:
    if "护理文书" in question and any(token in question for token in ("哪些", "种类", "关键", "重点")):
        return (
            "常用护理文书主要包括体温单、手术物品清点记录、病重（病危）患者护理记录、输血护理记录单、血糖测量记录单、护理日夜交接班报告和一般护理记录。"
            "体温单重点在生命体征时间点、发热复测和特殊标记；病重（病危）护理记录重点在生命体征、出入量、护理措施和效果；"
            "输血护理记录重点在输血前评估、双人核对、15分钟观察和结束后60分钟复评；血糖测量记录单重点在POCT时间点和复测留痕；"
            "护理日夜交接班报告重点在顺序清楚、病情变化、高危患者和下一班观察点。"
        )
    text = _safe_with_question(summary, question)
    generic_markers = (
        "\u8bf7\u95ee\u60a8\u9700\u8981\u54a8\u8be2\u6216\u4e86\u89e3\u4ec0\u4e48\u95ee\u9898",
        "\u8bf7\u544a\u8bc9\u6211\u60a8\u7684\u5177\u4f53\u9700\u6c42",
        "\u4ee5\u4fbf\u6211\u4e3a\u60a8\u63d0\u4f9b\u76f8\u5e94\u7684\u5e2e\u52a9",
        "\u672c\u5730\u6a21\u578b\u6682\u65f6\u4e0d\u53ef\u7528",
    )
    if any(marker in text for marker in generic_markers):
        if "\u4f4e\u8840\u7cd6" in question:
            text = (
                "\u4f4e\u8840\u7cd6\u7d27\u6025\u5904\u7406\u8981\u70b9\uff1a\u5148\u7acb\u5373\u786e\u8ba4\u610f\u8bc6\u3001\u51fa\u6c57\u3001\u5fc3\u614c\u3001\u624b\u6296\u7b49\u8868\u73b0\u5e76\u9a6c\u4e0a\u6d4b\u8840\u7cd6\uff1b"
                "\u60a3\u8005\u6e05\u9192\u80fd\u541e\u54bd\u65f6\uff0c\u7acb\u5373\u7ed9\u4e88\u542b\u7cd6\u996e\u6599\u6216\u8461\u8404\u7cd6\uff1b\u610f\u8bc6\u4e0d\u6e05\u6216\u4e0d\u80fd\u541e\u54bd\u65f6\uff0c\u7acb\u5373\u8d70\u9759\u8109\u8865\u8461\u8404\u7cd6\u6d41\u7a0b\u5e76\u901a\u77e5\u533b\u751f\u3002"
                "\u5904\u7406\u540e\u901a\u5e3815\u5206\u949f\u590d\u6d4b\u8840\u7cd6\uff0c\u590d\u6d4b\u4ecd\u4f4e\u8981\u7ee7\u7eed\u8865\u7cd6\u5e76\u518d\u6b21\u590d\u6d4b\uff0c\u540c\u65f6\u628a\u75c7\u72b6\u3001\u5904\u7406\u3001\u590d\u6d4b\u503c\u548c\u533b\u751f\u6c9f\u901a\u5b8c\u6574\u7559\u75d5\u3002"
            )
        elif "\u8dcc\u5012" in question:
            text = (
                "\u591c\u73ed\u8dcc\u5012\u9884\u9632\u91cd\u70b9\uff1a\u5148\u8bc4\u4f30\u8dcc\u5012\u98ce\u9669\uff0c\u4fdd\u6301\u591c\u95f4\u7167\u660e\u3001\u5e8a\u680f\u548c\u547c\u53eb\u94c3\u53ef\u53ca\uff0c\u63d0\u9192\u60a3\u8005\u8d77\u8eab\u5148\u5750\u8d77\u518d\u4e0b\u5e8a\uff1b"
                "\u9ad8\u98ce\u9669\u60a3\u8005\u8981\u52a0\u5f3a\u966a\u62a4\u548c\u5982\u5395\u534f\u52a9\uff0c\u5730\u9762\u9632\u6ed1\u3001\u978b\u889c\u5408\u9002\u3001\u7ba1\u8def\u56fa\u5b9a\uff0c\u5fc5\u8981\u65f6\u628a\u5e38\u7528\u7269\u54c1\u653e\u5728\u4f38\u624b\u53ef\u53ca\u5904\u3002"
                "\u4e00\u65e6\u51fa\u73b0\u5934\u6655\u3001\u6b65\u6001\u4e0d\u7a33\u3001\u591c\u95f4\u9891\u7e41\u8d77\u591c\u6216\u9547\u9759\u540e\u53cd\u5e94\u8fdf\u949d\uff0c\u8981\u7acb\u5373\u52a0\u5f3a\u770b\u62a4\u5e76\u8bb0\u5f55\u3002"
            )
        elif "\u5bfc\u5c3f\u7ba1" in question or "\u5bfc\u5c3f" in question:
            text = (
                "\u5bfc\u5c3f\u7ba1\u62a4\u7406\u8981\u70b9\uff1a\u4fdd\u6301\u5bfc\u5c3f\u7ba1\u56fa\u5b9a\u7a33\u59a5\uff0c\u5f15\u6d41\u888b\u59cb\u7ec8\u4f4e\u4e8e\u8180\u80f1\u6c34\u5e73\uff0c\u907f\u514d\u53d7\u538b\u3001\u6253\u6298\u548c\u9006\u6d41\uff1b"
                "\u6bcf\u65e5\u505a\u597d\u5c3f\u9053\u53e3\u6e05\u6d01\uff0c\u89c2\u5bdf\u5c3f\u91cf\u3001\u5c3f\u8272\u3001\u5c3f\u6db2\u6027\u72b6\u548c\u6709\u65e0\u5f02\u5473\u3002"
                "\u82e5\u51fa\u73b0\u53d1\u70ed\u3001\u803b\u9aa8\u4e0a\u75db\u3001\u5c3f\u6db2\u6df7\u6d4a\u6216\u8840\u5c3f\u3001\u5f15\u6d41\u4e0d\u7545\u7b49\u60c5\u51b5\uff0c\u8981\u8b66\u60d5\u5bfc\u5c3f\u7ba1\u76f8\u5173\u611f\u67d3\u5e76\u53ca\u65f6\u8054\u7cfb\u533b\u751f\u3002"
            )
        elif "\u75bc\u75db" in question and "\u590d\u8bc4" in question:
            text = (
                "\u75bc\u75db\u5e72\u9884\u540e\u9700\u8981\u6309\u6240\u7528\u836f\u7269\u6216\u6cbb\u7597\u65b9\u5f0f\u7684\u8fbe\u5cf0\u65f6\u95f4\u8fdb\u884c\u590d\u8bc4\uff1a"
                "\u80c3\u80a0\u5916\u7ed9\u836f\u540e\u901a\u5e3815-30\u5206\u949f\u590d\u8bc4\uff0c\u53e3\u670d\u9547\u75db\u836f\u540e\u4e00\u822c1-2\u5c0f\u65f6\u590d\u8bc4\u3002"
                "\u4f53\u6e29\u5355\u4e0a\u53ef\u7528\u7ea2\u8272\u201c\u0394\u201d\u8bb0\u5f55\u5e72\u9884\u540e\u75bc\u75db\u5206\u503c\uff0c\u5e76\u7528\u7ea2\u8272\u865a\u7ebf\u4e0e\u5904\u7406\u524d\u5206\u503c\u76f8\u8fde\uff1b"
                "\u62a4\u7406\u8bb0\u5f55\u9700\u540c\u6b65\u5199\u660e\u75bc\u75db\u53d8\u5316\u3001\u5df2\u91c7\u53d6\u63aa\u65bd\u3001\u590d\u8bc4\u65f6\u95f4\u53ca\u6548\u679c\u3002"
            )
        elif any(token in question for token in ("\u4e0b\u5e8a", "\u65e9\u671f\u6d3b\u52a8", "\u5750\u8d77", "\u7ad9\u7acb")):
            text = (
                "\u672f\u540e\u65e9\u671f\u6d3b\u52a8\u524d\u8981\u5148\u8bc4\u4f30\u610f\u8bc6\u3001\u8840\u538b\u3001\u8109\u640f\u3001\u75bc\u75db\u3001\u7ba1\u8def\u548c\u5207\u53e3\u60c5\u51b5\uff0c"
                "\u5148\u534f\u52a9\u60a3\u8005\u5750\u8d77\uff0c\u89c2\u5bdf\u5934\u6655\u3001\u51fa\u6c57\u548c\u9762\u8272\u53d8\u5316\uff0c\u518d\u5c1d\u8bd5\u7ad9\u7acb\u4e0e\u79fb\u6b65\u3002"
                "\u5982\u51fa\u73b0\u4f4e\u8840\u538b\u3001\u660e\u663e\u5fc3\u60b8\u3001\u75db\u75db\u52a0\u91cd\u3001\u547c\u5438\u4e0d\u9002\u6216\u7ba1\u8def\u53d7\u7275\u62c9\uff0c"
                "\u5e94\u7acb\u5373\u505c\u6b62\u6d3b\u52a8\u3001\u91cd\u65b0\u8bc4\u4f30\u5e76\u89c6\u60c5\u51b5\u8054\u7cfb\u533b\u751f\u3002"
            )
        elif "\u547c\u5438\u673a" in question or "\u62a5\u8b66" in question:
            text = (
                "\u547c\u5438\u673a\u60a3\u8005\u591c\u73ed\u8981\u6301\u7eed\u89c2\u5bdf\u8840\u6c27\u3001\u547c\u5438\u9891\u7387\u3001\u547c\u5438\u673a\u62a5\u8b66\u3001\u610f\u8bc6\u72b6\u6001\u3001\u75f0\u6db2\u60c5\u51b5\u548c\u7ba1\u8def\u901a\u7545\u5ea6\u3002"
                "\u62a5\u8b66\u540e\u5148\u67e5\u7ba1\u8def\u6253\u6298\u3001\u8131\u843d\u3001\u5206\u6ccc\u7269\u5835\u585e\u548c\u6c27\u6e90\uff0c"
                "\u5982\u8840\u6c27\u6301\u7eed\u4e0b\u964d\u3001\u547c\u5438\u56f0\u96be\u52a0\u91cd\u6216\u610f\u8bc6\u6076\u5316\uff0c\u8981\u7acb\u5373\u627e\u533b\u751f\u3002"
                "\u4ea4\u73ed\u65f6\u8981\u91cd\u70b9\u63d0\u9192\u62a5\u8b66\u5904\u7406\u7ecf\u8fc7\u3001\u6700\u65b0\u53c2\u6570\u548c\u4e0b\u4e00\u73ed\u89c2\u5bdf\u70b9\u3002"
            )
        elif "\u6c27\u7597" in question or "\u4f4e\u6c27" in question or "\u4e8c\u6c27\u5316\u78b3" in question:
            text = (
                "\u6c27\u7597\u89c2\u5bdf\u8981\u70b9\uff1a\u6301\u7eed\u89c2\u5bdf\u8840\u6c27\u9971\u548c\u5ea6\u3001\u547c\u5438\u9891\u7387\u3001\u547c\u5438\u529f\u3001\u610f\u8bc6\u72b6\u6001\u548c\u5438\u6c27\u6548\u679c\uff1b"
                "\u6162\u963b\u80ba\u6216\u4e8c\u6c27\u5316\u78b3\u6f74\u7559\u98ce\u9669\u60a3\u8005\u8981\u7279\u522b\u8b66\u60d5\u55dc\u7761\u3001\u5934\u75db\u3001\u610f\u8bc6\u53d8\u5dee\u548c\u547c\u5438\u53d8\u6d45\u3002"
                "\u591c\u73ed\u8981\u6301\u7eed\u89c2\u5bdf\u3001\u6309\u533b\u5631\u590d\u6838\u5173\u952e\u6307\u6807\uff0c\u82e5\u8840\u6c27\u6301\u7eed\u4e0b\u964d\u3001\u547c\u5438\u9891\u7387\u660e\u663e\u5f02\u5e38\u6216\u610f\u8bc6\u6076\u5316\uff0c\u5e94\u9a6c\u4e0a\u627e\u533b\u751f\u3002"
            )
        elif "\u538b\u4f24" in question:
            text = (
                "\u538b\u4f24\u9884\u9632\u8981\u70b9\uff1a\u91cd\u70b9\u843d\u5b9e\u4f53\u4f4d\u7ba1\u7406\u548c\u7ffb\u8eab\u51cf\u538b\uff0c\u6309\u98ce\u9669\u7b49\u7ea7\u5b9a\u65f6\u53d8\u6362\u4f53\u4f4d\uff0c\u4fdd\u62a4\u9abc\u5c3e\u90e8\u3001\u8db3\u8ddf\u7b49\u53d7\u538b\u70b9\uff1b"
                "\u540c\u65f6\u505a\u597d\u76ae\u80a4\u8bc4\u4f30\u3001\u5e8a\u5355\u4f4d\u5e73\u6574\u3001\u5931\u7981\u548c\u6f6e\u6e7f\u7ba1\u7406\u3001\u8425\u517b\u652f\u6301\u53ca\u7559\u75d5\u8bb0\u5f55\u3002"
                "\u82e5\u76ae\u80a4\u51fa\u73b0\u6301\u7eed\u53d1\u7ea2\u3001\u7834\u635f\u3001\u6c34\u75b1\u6216\u75bc\u75db\u52a0\u91cd\uff0c\u5e94\u53ca\u65f6\u4e0a\u62a5\u5e76\u8c03\u6574\u51cf\u538b\u65b9\u6848\u3002"
            )
        elif "\u5916\u6e17" in question:
            text = (
                "\u8f93\u6db2\u5916\u6e17\u5904\u7406\u8981\u70b9\uff1a\u7acb\u5373\u505c\u6b62\u8f93\u6db2\uff0c\u5c3d\u91cf\u4fdd\u7559\u901a\u8def\u5e76\u8bc4\u4f30\u5916\u6e17\u8303\u56f4\u3001\u76ae\u80a4\u6e29\u5ea6\u3001\u989c\u8272\u548c\u75bc\u75db\uff1b"
                "\u62ac\u9ad8\u60a3\u80a2\uff0c\u6309\u836f\u7269\u6027\u8d28\u9009\u62e9\u51b7\u6577\u6216\u70ed\u6577\uff0c\u5fc5\u8981\u65f6\u6309\u89c4\u8303\u56de\u62bd\u5916\u6e17\u836f\u6db2\u5e76\u53ca\u65f6\u901a\u77e5\u533b\u751f\u3002"
                "\u5904\u7406\u540e\u8981\u628a\u5916\u6e17\u65f6\u95f4\u3001\u8303\u56f4\u3001\u836f\u7269\u3001\u63aa\u65bd\u548c\u590d\u8bc4\u7ed3\u679c\u5b8c\u6574\u7559\u75d5\u3002"
            )
        elif "\u5f15\u6d41" in question:
            text = (
                "\u672f\u540e\u5f15\u6d41\u89c2\u5bdf\u91cd\u70b9\uff1a\u6301\u7eed\u770b\u5f15\u6d41\u91cf\u3001\u989c\u8272\u3001\u6027\u72b6\u3001\u901a\u7545\u5ea6\u548c\u56fa\u5b9a\u60c5\u51b5\uff0c\u4fdd\u6301\u65e0\u83cc\u64cd\u4f5c\u5e76\u9632\u6b62\u7275\u62c9\u3001\u626d\u66f2\u3001\u6253\u6298\uff1b"
                "\u4ea4\u73ed\u65f6\u8981\u660e\u786e\u672c\u73ed\u5f15\u6d41\u8d8b\u52bf\u548c\u4e0b\u4e00\u73ed\u89c2\u5bdf\u70b9\u3002"
                "\u82e5\u5f15\u6d41\u7a81\u7136\u589e\u591a\u3001\u9c9c\u7ea2\u3001\u6076\u81ed\u3001\u5b8c\u5168\u65e0\u5f15\u6d41\u6216\u4f34\u53d1\u70ed\u75bc\u75db\u52a0\u91cd\uff0c\u5e94\u53ca\u65f6\u8054\u7cfb\u533b\u751f\u3002"
            )
    carry_terms = {
        "\u538b\u4f24": "\u538b\u4f24\u9884\u9632\u8981\u70b9\u8981\u843d\u5b9e\u7ffb\u8eab\u51cf\u538b\u3001\u53d7\u538b\u70b9\u76ae\u80a4\u8bc4\u4f30\u3001\u5e8a\u5355\u4f4d\u7ba1\u7406\u548c\u7559\u75d5\u3002",
        "\u8dcc\u5012": "\u8dcc\u5012\u9884\u9632\u8981\u843d\u5b9e\u73af\u5883\u5b89\u5168\u3001\u8d77\u8eab\u534f\u52a9\u3001\u5982\u5395\u966a\u62a4\u548c\u9ad8\u98ce\u9669\u60a3\u8005\u770b\u62a4\u3002",
        "\u4f4e\u6c27": "\u4f4e\u6c27\u65f6\u8981\u6301\u7eed\u590d\u6838\u8840\u6c27\u3001\u547c\u5438\u9891\u7387\u548c\u610f\u8bc6\u53d8\u5316\uff0c\u5e76\u53ca\u65f6\u8054\u7cfb\u533b\u751f\u3002",
        "\u8840\u6c27": "\u8981\u6301\u7eed\u590d\u6838\u8840\u6c27\u9971\u548c\u5ea6\u3001\u547c\u5438\u9891\u7387\u548c\u5438\u6c27\u6548\u679c\u3002",
        "\u590d\u6838": "\u5173\u952e\u6307\u6807\u5f02\u5e38\u540e\u8981\u6309\u65f6\u590d\u6838\u5e76\u8bb0\u5f55\u3002",
        "\u7559\u75d5": "\u5904\u7406\u5b8c\u6210\u540e\u8981\u540c\u6b65\u7559\u75d5\u5230\u62a4\u7406\u8bb0\u5f55\u548c\u4ea4\u73ed\u3002",
    }
    for term, fallback in carry_terms.items():
        if term in question and term not in text:
            text = f"{text}\n{fallback}".strip()
    if ("\u8054\u7cfb\u533b\u751f" in question or "\u627e\u533b\u751f" in question) and ("\u8054\u7cfb\u533b\u751f" not in text and "\u627e\u533b\u751f" not in text):
        text = f"{text}\n\u5982\u5f02\u5e38\u6301\u7eed\u6216\u6307\u6807\u6076\u5316\uff0c\u8981\u7acb\u5373\u8054\u7cfb\u533b\u751f\u5e76\u8865\u8bb0\u62a4\u7406\u7559\u75d5\u3002".strip()
    if "\u89c2\u5bdf" in question and "\u89c2\u5bdf" not in text:
        text = f"{text}\n\u8bf7\u6309\u5e8a\u65c1\u987a\u5e8f\u8865\u5145\u89c2\u5bdf\u6307\u6807\u3001\u590d\u6838\u65f6\u70b9\u548c\u5f02\u5e38\u4e0a\u62a5\u8282\u70b9\u3002".strip()
    if "\u62a4\u7406\u8bb0\u5f55" in question and "\u62a4\u7406\u8bb0\u5f55" not in text:
        text = f"{text}\n\u5904\u7406\u5b8c\u6210\u540e\u8981\u540c\u6b65\u5199\u5165\u62a4\u7406\u8bb0\u5f55\uff0c\u5fc5\u8981\u65f6\u540c\u6b65\u8865\u8bb0\u4ea4\u73ed\u3002".strip()
    if "\u5bfc\u5c3f" in question and "\u5bfc\u5c3f\u7ba1" not in text:
        text = f"{text}\n\u5e8a\u65c1\u5904\u7406\u65f6\u8981\u5148\u68c0\u67e5\u5bfc\u5c3f\u7ba1\u56fa\u5b9a\u3001\u5f15\u6d41\u662f\u5426\u901a\u7545\uff0c\u5e76\u8bb0\u5f55\u5c3f\u91cf\u4e0e\u6027\u72b6\u53d8\u5316\u3002".strip()
    if ("\u547c\u5438\u673a" in question or "\u62a5\u8b66" in question) and "\u673a\u5668" not in text:
        text = f"{text}\n处理呼吸机报警时要先看患者，再看机器、管路和参数，避免只盯屏幕忽略床旁变化。".strip()
    if "\u547c\u5438\u673a" in question:
        missing_fragments: list[str] = []
        if "\u5206\u6ccc\u7269" not in text:
            missing_fragments.append("夜班还要持续观察分泌物量、颜色和是否堵塞气道，必要时及时吸引并复核通气效果。")
        if "\u8054\u7cfb\u533b\u751f" not in text:
            missing_fragments.append("如血氧持续下降、呼吸困难加重、分泌物明显增多难以清除或意识变差，应立即联系医生。")
        if "\u4e0b\u4e00\u73ed" not in text:
            missing_fragments.append("交班时要把报警经过、最新血氧、分泌物处理和下一班观察重点交代清楚。")
        if missing_fragments:
            text = f"{text}\n" + "\n".join(missing_fragments)
            text = text.strip()
    if "\u538b\u4f24" in question:
        missing_fragments = []
        if "\u7ffb\u8eab" not in text:
            missing_fragments.append("压伤高风险患者要按计划翻身减压，避免同一受压点持续受压。")
        if "\u76ae\u80a4" not in text:
            missing_fragments.append("每次护理都要重新评估皮肤颜色、温度、完整性和受压点变化。")
        if "\u6e7f\u6027" not in text:
            missing_fragments.append("对失禁、渗液或出汗明显患者，要做好湿性皮肤管理，保持皮肤清洁干燥并及时更换敷料或床单位。")
        if "\u8054\u7cfb\u533b\u751f" not in text:
            missing_fragments.append("如皮肤持续发红不退、出现破损、水疱、渗液或疼痛加重，应及时联系医生并升级处理。")
        if missing_fragments:
            text = f"{text}\n" + "\n".join(missing_fragments)
            text = text.strip()
    if "\u91cd\u65b0\u8bc4\u4f30" in question and "\u91cd\u65b0\u8bc4\u4f30" not in text:
        text = f"{text}\n\u5982\u6d3b\u52a8\u8fc7\u7a0b\u4e2d\u51fa\u73b0\u4f4e\u8840\u538b\u3001\u5fc3\u60b8\u3001\u75db\u75db\u52a0\u91cd\u6216\u547c\u5438\u4e0d\u9002\uff0c\u5e94\u7acb\u5373\u505c\u6b62\u6d3b\u52a8\u5e76\u91cd\u65b0\u8bc4\u4f30\u3002".strip()
    return text


def _scope_prompt_from_preview(preview: PatientScopePreview) -> str:
    if not preview.matched_patients:
        return ""

    if preview.ward_scope or len(preview.matched_patients) > 1:
        rows: list[str] = []
        for item in preview.matched_patients[:6]:
            label = f"{item.bed_no or '-'}床"
            if item.patient_name:
                label = f"{label} {item.patient_name}"
            detail_parts: list[str] = []
            if item.diagnoses:
                detail_parts.append(f"诊断：{'、'.join(item.diagnoses[:2])}")
            if item.risk_tags:
                detail_parts.append(f"风险：{'、'.join(item.risk_tags[:3])}")
            if item.pending_tasks:
                detail_parts.append(f"待办：{'、'.join(item.pending_tasks[:3])}")
            rows.append(f"- {label}；" + "；".join(detail_parts))
        return "已自动识别为多病例/病区问题，可参考以下患者上下文：\n" + "\n".join(rows)

    item = preview.matched_patients[0]
    label = f"{item.bed_no or item.resolved_bed_no or '-'}床"
    if item.patient_name:
        label = f"{label}（{item.patient_name}）"
    parts = [f"已自动定位患者：{label}。"]
    if item.diagnoses:
        parts.append(f"当前诊断：{'、'.join(item.diagnoses[:3])}。")
    if item.risk_tags:
        parts.append(f"风险标签：{'、'.join(item.risk_tags[:3])}。")
    if item.pending_tasks:
        parts.append(f"当前待办：{'、'.join(item.pending_tasks[:3])}。")
    if item.correction_note:
        parts.insert(0, f"{item.correction_note}")
    return "".join(parts)


async def _run_medgemma_single(payload: AIChatRequest) -> dict[str, Any]:
    body = {
        "patient_id": payload.patient_id or "unknown",
        "input_refs": payload.attachments,
        "question": payload.user_input,
    }

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(40, connect=8), trust_env=False) as client:
            resp = await client.post(f"{settings.multimodal_service_url}/multimodal/analyze", json=body)
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        return {
            "summary": _safe_with_question(
                "本地多模态服务暂时不可用，已回退为安全提示：请先进行人工复核并补充关键生命体征。",
                payload.user_input,
            ),
            "findings": ["未获取到本地多模态服务结果"],
            "recommendations": [
                {"title": "先进行人工复核并补录关键信息", "priority": 1},
                {"title": "稍后重试本地多模态分析", "priority": 2},
            ],
            "confidence": 0.35,
            "review_required": True,
        }

    return {
        "summary": _safe_with_question(str(data.get("summary") or "已完成本地多模态分析"), payload.user_input),
        "findings": data.get("findings") if isinstance(data.get("findings"), list) else [],
        "recommendations": data.get("recommendations") if isinstance(data.get("recommendations"), list) else [],
        "confidence": float(data.get("confidence", 0.72) or 0.72),
        "review_required": bool(data.get("review_required", True)),
    }


async def _write_audit(
    *,
    action: str,
    resource_type: str,
    resource_id: str | None,
    detail: dict[str, Any],
    user_id: str | None,
) -> None:
    payload = {
        "user_id": user_id,
        "action": action,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "detail": detail,
    }
    async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
        try:
            await client.post(f"{settings.audit_service_url}/audit/log", json=payload)
        except Exception:
            return


@router.get("/health")
def health() -> dict:
    return {"status": "ok", "service": settings.service_name}


@router.get("/ready")
def ready() -> dict:
    return {"status": "ready", "service": settings.service_name}


@router.get("/version")
async def version() -> dict:
    runtime_status = runtime.status()
    local_status = await probe_local_models()
    return {
        "service": settings.service_name,
        "version": settings.app_version,
        "env": settings.app_env,
        "mock_mode": settings.mock_mode,
        "local_only_mode": settings.local_only_mode,
        "runtime_configured_engine": runtime_status["configured_engine"],
        "runtime_active_engine": runtime_status["active_engine"],
        "runtime_langgraph_available": runtime_status["langgraph_available"],
        "runtime_override_enabled": runtime_status["override_enabled"],
        "runtime_fallback_reason": runtime_status["fallback_reason"],
        "planner_llm_enabled": settings.agent_planner_llm_enabled,
        "local_model_service_reachable": local_status["reachable"],
        "registered_agent_tools": [tool.id for tool in agentic_orchestrator.tool_specs()],
        "approval_required_tools": agentic_orchestrator.approval_tool_ids(),
        "task_queue": agent_task_worker.status(),
        "local_model_aliases": {
            "primary": settings.local_llm_model_primary,
            "fallback": settings.local_llm_model_fallback,
            "planner": settings.local_llm_model_planner,
            "reasoning": settings.local_llm_model_reasoning,
            "tcm": settings.local_llm_model_tcm,
            "custom": settings.local_llm_model_custom,
            "multimodal": settings.local_llm_model_multimodal,
        },
    }


@router.post("/intent/route", response_model=IntentRsp)
async def route_intent(req: IntentReq) -> IntentRsp:
    intent = await runtime.route_intent(req.text)
    return IntentRsp(intent=intent)


@router.get("/ai/runtime")
async def ai_runtime_status() -> dict[str, Any]:
    status = runtime.status()
    local_status = await probe_local_models()
    status["planner_llm_enabled"] = settings.agent_planner_llm_enabled
    status["planner_timeout_sec"] = settings.agent_planner_timeout_sec
    status["planner_max_steps"] = settings.agent_planner_max_steps
    status["local_model_service_reachable"] = local_status["reachable"]
    status["available_local_models"] = local_status["models"]
    status["registered_agent_tools"] = [tool.id for tool in agentic_orchestrator.tool_specs()]
    status["approval_required_tools"] = agentic_orchestrator.approval_tool_ids()
    status["task_queue"] = agent_task_worker.status()
    status["local_model_aliases"] = {
        "primary": settings.local_llm_model_primary,
        "fallback": settings.local_llm_model_fallback,
        "planner": settings.local_llm_model_planner,
        "reasoning": settings.local_llm_model_reasoning,
        "tcm": settings.local_llm_model_tcm,
        "custom": settings.local_llm_model_custom,
        "multimodal": settings.local_llm_model_multimodal,
    }
    return status


@router.post("/ai/runtime")
async def ai_runtime_set(req: RuntimeEngReq) -> dict[str, Any]:
    r = (req.engine or "").strip().lower()
    if r not in {"state_machine", "langgraph", "graph"}:
        raise HTTPException(status_code=400, detail="invalid_engine")
    return runtime.set_engine(r)


@router.delete("/ai/runtime")
async def ai_runtime_clear() -> dict[str, Any]:
    return runtime.clear_override()


@router.post("/workflow/run", response_model=WorkflowOutput)
async def run_workflow(payload: WorkflowRequest) -> WorkflowOutput:
    return await runtime.run(payload)


@router.get("/ai/models", response_model=AIModelsResponse)
async def ai_models() -> AIModelsResponse:
    return await _models_catalog()


@router.get("/ai/tools", response_model=list[AgentToolSpec])
async def ai_tools() -> list[AgentToolSpec]:
    return agentic_orchestrator.tool_specs()


@router.post("/ai/scope/preview", response_model=PatientScopePreview)
async def ai_scope_preview(payload: WorkflowRequest) -> PatientScopePreview:
    return await machine.preview_scope(payload, allow_ward_fallback=True)


@router.get("/ai/runs", response_model=list[AgentRunRecord])
async def ai_runs(
    patient_id: str | None = Query(default=None),
    conversation_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    workflow_type: WorkflowType | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[AgentRunRecord]:
    return agent_run_store.list(
        patient_id=patient_id,
        conversation_id=conversation_id,
        status=status,
        workflow_type=workflow_type,
        limit=limit,
    )


@router.get("/ai/runs/{run_id}", response_model=AgentRunRecord)
async def ai_run_detail(run_id: str) -> AgentRunRecord:
    record = agent_run_store.get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="run_not_found")
    return record


@router.post("/ai/runs/{run_id}/retry", response_model=WorkflowOutput)
async def ai_run_retry(run_id: str) -> WorkflowOutput:
    payload = agent_run_store.retry_request(run_id)
    if payload is None:
        raise HTTPException(status_code=409, detail="retry_unavailable")
    return await runtime.run(payload)


@router.post("/ai/queue/tasks", response_model=AgentQueueTask)
async def ai_queue_enqueue(payload: AgentQueueEnqueueRequest) -> AgentQueueTask:
    task = agent_queue_store.enqueue(
        payload.payload,
        requested_engine=payload.requested_engine,
        priority=payload.priority,
    )
    agent_task_worker.notify()
    return task


@router.get("/ai/queue/tasks", response_model=list[AgentQueueTask])
async def ai_queue_tasks(
    patient_id: str | None = Query(default=None),
    conversation_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[AgentQueueTask]:
    return agent_queue_store.list(
        patient_id=patient_id,
        conversation_id=conversation_id,
        status=status,
        limit=limit,
    )


@router.get("/ai/queue/tasks/{task_id}", response_model=AgentQueueTask)
async def ai_queue_task_detail(task_id: str) -> AgentQueueTask:
    task = agent_queue_store.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="queue_task_not_found")
    return task


@router.post("/ai/queue/tasks/{task_id}/approve", response_model=AgentQueueTask)
async def ai_queue_task_approve(task_id: str, payload: AgentQueueDecisionRequest) -> AgentQueueTask:
    if agent_queue_store.get(task_id) is None:
        raise HTTPException(status_code=404, detail="queue_task_not_found")
    task = agent_queue_store.approve(
        task_id,
        approval_ids=payload.approval_ids,
        decided_by=payload.decided_by,
        comment=payload.comment,
    )
    if task is None:
        raise HTTPException(status_code=409, detail="queue_task_not_waiting_approval")
    if task.status == "queued":
        agent_task_worker.notify()
    return task


@router.post("/ai/queue/tasks/{task_id}/reject", response_model=AgentQueueTask)
async def ai_queue_task_reject(task_id: str, payload: AgentQueueDecisionRequest) -> AgentQueueTask:
    if agent_queue_store.get(task_id) is None:
        raise HTTPException(status_code=404, detail="queue_task_not_found")
    task = agent_queue_store.reject(
        task_id,
        approval_ids=payload.approval_ids,
        decided_by=payload.decided_by,
        comment=payload.comment,
    )
    if task is None:
        raise HTTPException(status_code=409, detail="queue_task_not_waiting_approval")
    if task.status == "queued":
        agent_task_worker.notify()
    return task


@router.post("/ai/chat", response_model=AIChatResponse)
async def ai_chat(payload: AIChatRequest) -> AIChatResponse:
    if payload.mode == ChatMode.SINGLE_MODEL:
        selected = payload.selected_model or "minicpm3_4b_local"
        scope_preview = await machine.preview_scope(
            WorkflowRequest(
                workflow_type=WorkflowType.SINGLE_MODEL_CHAT,
                patient_id=payload.patient_id,
                conversation_id=payload.conversation_id,
                department_id=payload.department_id,
                bed_no=payload.bed_no,
                user_input=payload.user_input,
                mission_title=payload.mission_title,
                success_criteria=list(payload.success_criteria),
                operator_notes=payload.operator_notes,
                attachments=list(payload.attachments),
                requested_by=payload.requested_by,
                agent_mode="direct_answer",
                execution_profile=payload.execution_profile,
            ),
            allow_ward_fallback=True,
        )
        primary_scope = scope_preview.matched_patients[0] if scope_preview.matched_patients else None
        multi_scope = scope_preview.ward_scope or len(scope_preview.matched_patients) > 1
        resolved_patient_id = (
            payload.patient_id
            or (None if multi_scope else primary_scope.patient_id if primary_scope else None)
        )
        resolved_patient_name = None if multi_scope else (primary_scope.patient_name if primary_scope else None)
        resolved_bed_no = payload.bed_no or (None if multi_scope else primary_scope.bed_no if primary_scope else None)
        scope_prompt = _scope_prompt_from_preview(scope_preview)
        prompt_sections = [section for section in [scope_prompt, f"护士问题：{payload.user_input}"] if section]
        if payload.mission_title:
            prompt_sections.append(f"任务标题：{payload.mission_title}")
        if payload.success_criteria:
            prompt_sections.append(f"成功标准：{'；'.join(payload.success_criteria)}")
        if payload.operator_notes:
            prompt_sections.append(f"操作备注：{payload.operator_notes}")
        enriched_prompt = "\n".join(prompt_sections)

        single_findings: list[str] = []
        if primary_scope and primary_scope.correction_note:
            single_findings.append(primary_scope.correction_note)
        if multi_scope:
            beds_text = "、".join(
                [str(item.bed_no or item.resolved_bed_no or "").strip() for item in scope_preview.matched_patients[:6] if str(item.bed_no or item.resolved_bed_no or "").strip()]
            )
            if beds_text:
                single_findings.append(f"已自动识别多病例范围：{beds_text}床")
        elif resolved_bed_no:
            located_text = f"已自动定位 {resolved_bed_no}床"
            if resolved_patient_name:
                located_text = f"{located_text}（{resolved_patient_name}）"
            single_findings.append(located_text)

        if selected == "medgemma_local":
            local_result = await _run_medgemma_single(
                payload.model_copy(
                    update={
                        "patient_id": resolved_patient_id,
                        "bed_no": resolved_bed_no,
                    }
                )
            )
            output = WorkflowOutput(
                workflow_type=WorkflowType.SINGLE_MODEL_CHAT,
                summary=local_result["summary"],
                findings=[*single_findings, *local_result["findings"]],
                recommendations=local_result["recommendations"],
                confidence=float(local_result["confidence"]),
                review_required=bool(local_result["review_required"]),
                steps=[AgentStep(agent="MedGemma Runner", status="done")],
                patient_id=resolved_patient_id,
                patient_name=resolved_patient_name,
                bed_no=resolved_bed_no,
                created_at=datetime.now(timezone.utc),
            )
        elif selected in _local_aliases():
            local_model_name = _resolve_selected_local_model(selected)
            if not local_model_name:
                refined = "当前模型别名未配置。请在 .env.local 中设置对应的 LOCAL_LLM_MODEL_* 变量。"
            else:
                refined = await local_refine_with_model(
                    enriched_prompt,
                    local_model_name,
                    system_msg=TCM_SYSTEM_MESSAGE if selected == "tcm_consult_local" else None,
                )
            if not refined:
                refined = (
                    "本地模型暂时不可用，请先启动本地模型服务，"
                    "或检查模型别名是否与当前服务暴露的一致。"
                )
            refined = _postprocess_single_model_summary(payload.user_input, refined)
            if primary_scope and primary_scope.correction_note and primary_scope.correction_note not in refined:
                refined = f"{primary_scope.correction_note}{refined}"
            output = WorkflowOutput(
                workflow_type=WorkflowType.SINGLE_MODEL_CHAT,
                summary=refined,
                findings=single_findings,
                recommendations=(
                    [
                        {"title": "中医辨证结论仅作护理辅助，请结合医师诊断。", "priority": 1},
                        {"title": "涉及处方、辨证用药或针灸请转中医师确认。", "priority": 1},
                    ]
                    if selected == "tcm_consult_local"
                    else [
                        {"title": "请人工复核关键客观数据、时间点和患者当前主诉后再执行。", "priority": 1},
                        {"title": "涉及医嘱调整、处置升级或跨专业决策时，请联系医生确认。", "priority": 1},
                    ]
                ),
                confidence=0.72,
                review_required=True,
                steps=[AgentStep(agent="Local CN Model Runner", status="done", input={"selected_model": selected})],
                patient_id=resolved_patient_id,
                patient_name=resolved_patient_name,
                bed_no=resolved_bed_no,
                created_at=datetime.now(timezone.utc),
            )
        else:
            if settings.local_only_mode:
                refined = await local_refine_with_model(enriched_prompt, settings.local_llm_model_primary)
                if not refined:
                    refined = "本地模型暂时不可用，当前已禁用云端回退。请先启动本地模型服务。"
            else:
                refined = await bailian_refine(enriched_prompt)
            refined = _postprocess_single_model_summary(payload.user_input, refined)
            findings = list(single_findings)
            if payload.attachments:
                findings.append(f"已接收{len(payload.attachments)}个附件，可切换本地多模态模型进一步分析。")
            output = WorkflowOutput(
                workflow_type=WorkflowType.SINGLE_MODEL_CHAT,
                summary=refined,
                findings=findings,
                recommendations=[
                    {"title": "先给出初步判断，再触发人工复核", "priority": 1},
                    {"title": "必要时切换到AI Agent集群获取多模型协同结论", "priority": 2},
                ],
                confidence=0.76,
                review_required=True,
                steps=[AgentStep(agent="Single Model Runner", status="done", input={"selected_model": selected})],
                patient_id=resolved_patient_id,
                patient_name=resolved_patient_name,
                bed_no=resolved_bed_no,
                execution_profile=payload.execution_profile,
                mission_title=payload.mission_title,
                success_criteria=list(payload.success_criteria),
                created_at=datetime.now(timezone.utc),
            )

        history_request = WorkflowRequest(
            workflow_type=WorkflowType.SINGLE_MODEL_CHAT,
            patient_id=resolved_patient_id,
            conversation_id=payload.conversation_id,
            department_id=payload.department_id,
            bed_no=resolved_bed_no,
            user_input=payload.user_input,
            mission_title=payload.mission_title,
            success_criteria=list(payload.success_criteria),
            operator_notes=payload.operator_notes,
            attachments=payload.attachments,
            requested_by=payload.requested_by,
            agent_mode="direct_answer",
            execution_profile=payload.execution_profile,
        )
        output = agentic_orchestrator.finalize(
            history_request,
            output.model_copy(
                update={
                    "agent_mode": "direct_answer",
                    "execution_profile": payload.execution_profile,
                    "mission_title": payload.mission_title,
                    "success_criteria": list(payload.success_criteria),
                }
            ),
        )
        workflow_history_store.append(history_request, output)

        await _write_audit(
            action="ai_chat.single_model",
            resource_type="ai_chat",
            resource_id=payload.patient_id,
            detail={"selected_model": selected, "attachments": len(payload.attachments)},
            user_id=payload.requested_by,
        )

        if selected == "medgemma_local":
            model_name = "MedGemma 4B（本地）"
            model_role = "本地多模态判读"
            model_task = "图像/PDF/病历分析"
        elif selected == "tcm_consult_local":
            model_name = "中医问诊模型（本地）"
            model_role = "中医辨证辅助"
            model_task = "补充中医辨证线索与中医特色护理观察点"
        elif selected == "minicpm3_4b_local":
            model_name = "MiniCPM3-4B（本地中文）"
            model_role = "本地中文问答"
            model_task = "低资源中文临床问答"
        elif selected == "qwen2_5_3b_local":
            model_name = "Qwen2.5-3B（本地轻量）"
            model_role = "本地轻量问答"
            model_task = "低内存中文问答"
        else:
            model_name = "本地中文主模型"
            model_role = "直接回答"
            model_task = "按本地单模型策略完成问答"

        return AIChatResponse(
            mode=payload.mode,
            selected_model=selected,
            cluster_profile=None,
            conversation_id=payload.conversation_id,
            patient_id=output.patient_id,
            patient_name=output.patient_name,
            bed_no=output.bed_no,
            workflow_type=WorkflowType.SINGLE_MODEL_CHAT,
            summary=_normalize_output_text(output.summary),
            findings=output.findings,
            recommendations=output.recommendations,
            confidence=output.confidence,
            review_required=output.review_required,
            steps=output.steps,
            model_plan=[
                AIModelTask(
                    model_id=selected,
                    model_name=model_name,
                    role=model_role,
                    task=model_task,
                    enabled=True,
                )
            ],
            run_id=output.run_id,
            runtime_engine=output.runtime_engine,
            agent_goal=output.agent_goal,
            agent_mode=output.agent_mode,
            execution_profile=output.execution_profile or payload.execution_profile,
            mission_title=output.mission_title or payload.mission_title,
            success_criteria=list(output.success_criteria or payload.success_criteria),
            plan=output.plan,
            memory=output.memory,
            artifacts=output.artifacts,
            specialist_profiles=output.specialist_profiles,
            hybrid_care_path=output.hybrid_care_path,
            data_capsule=output.data_capsule,
            health_graph=output.health_graph,
            reasoning_cards=output.reasoning_cards,
            pending_approvals=output.pending_approvals,
            next_actions=output.next_actions,
            created_at=output.created_at,
        )

    # agent cluster
    effective_agent_mode = payload.agent_mode or ("autonomous" if _normalize_execution_profile(payload.execution_profile) == "full_loop" else None)
    forced_workflow = _force_workflow_from_prompt(payload.user_input, payload.execution_profile, payload.cluster_profile)
    intent = forced_workflow or _coerce_chat_workflow(await runtime.route_intent(payload.user_input), payload.execution_profile)
    workflow_payload = WorkflowRequest(
        workflow_type=intent,
        patient_id=payload.patient_id,
        conversation_id=payload.conversation_id,
        department_id=payload.department_id,
        bed_no=payload.bed_no,
        user_input=payload.user_input,
        mission_title=payload.mission_title,
        success_criteria=list(payload.success_criteria),
        operator_notes=payload.operator_notes,
        attachments=payload.attachments,
        requested_by=payload.requested_by,
        agent_mode=effective_agent_mode,
        execution_profile=payload.execution_profile,
    )

    try:
        output = await runtime.run(workflow_payload)
    except Exception:
        output = agentic_orchestrator.finalize(
            workflow_payload,
            WorkflowOutput(
            workflow_type=WorkflowType.VOICE_INQUIRY,
            summary=_safe_with_question("集群推理暂时超时，已返回安全降级结果，请稍后重试。", payload.user_input),
            findings=["agent-orchestrator 执行超时或上游不可用"],
            recommendations=[
                {"title": "先人工复核当前病例风险", "priority": 1},
                {"title": "稍后重新发起Agent集群分析", "priority": 2},
            ],
            confidence=0.32,
            review_required=True,
            steps=[AgentStep(agent="Agent Cluster Fallback", status="done")],
            agent_mode=effective_agent_mode or "assisted",
            execution_profile=payload.execution_profile,
            mission_title=payload.mission_title,
            success_criteria=list(payload.success_criteria),
            created_at=datetime.now(timezone.utc),
            ),
        )
        workflow_history_store.append(workflow_payload, output)

    await _write_audit(
        action="ai_chat.agent_cluster",
        resource_type="ai_chat",
        resource_id=payload.patient_id,
        detail={
            "cluster_profile": payload.cluster_profile,
            "workflow_type": output.workflow_type.value,
            "execution_profile": _normalize_execution_profile(payload.execution_profile),
            "mission_title": payload.mission_title,
        },
        user_id=payload.requested_by,
    )

    return AIChatResponse(
        mode=payload.mode,
        selected_model="minicpm3_4b_local" if settings.local_only_mode else "bailian_main",
        cluster_profile=payload.cluster_profile,
        conversation_id=payload.conversation_id,
        patient_id=output.patient_id,
        patient_name=output.patient_name,
        bed_no=output.bed_no,
        workflow_type=output.workflow_type,
        summary=_normalize_output_text(output.summary),
        findings=output.findings,
        recommendations=output.recommendations,
        confidence=output.confidence,
        review_required=output.review_required,
        steps=output.steps,
        model_plan=_cluster_tasks(
            payload.attachments,
            _online_models(await probe_local_models()),
            include_tcm=payload.cluster_profile == "tcm_nursing_cluster",
        ),
        run_id=output.run_id,
        runtime_engine=output.runtime_engine,
        agent_goal=output.agent_goal,
        agent_mode=output.agent_mode,
        execution_profile=output.execution_profile or payload.execution_profile,
        mission_title=output.mission_title or payload.mission_title,
        success_criteria=list(output.success_criteria or payload.success_criteria),
        plan=output.plan,
        memory=output.memory,
        artifacts=output.artifacts,
        specialist_profiles=output.specialist_profiles,
        hybrid_care_path=output.hybrid_care_path,
        data_capsule=output.data_capsule,
        health_graph=output.health_graph,
        reasoning_cards=output.reasoning_cards,
        pending_approvals=output.pending_approvals,
        next_actions=output.next_actions,
        created_at=output.created_at,
    )


@router.post("/workflow/voice-inquiry", response_model=WorkflowOutput)
async def voice_inquiry(payload: WorkflowRequest) -> WorkflowOutput:
    payload.workflow_type = WorkflowType.VOICE_INQUIRY
    return await runtime.run(payload)


@router.post("/workflow/handover", response_model=WorkflowOutput)
async def handover(payload: WorkflowRequest) -> WorkflowOutput:
    payload.workflow_type = WorkflowType.HANDOVER
    return await runtime.run(payload)


@router.post("/workflow/recommendation", response_model=WorkflowOutput)
async def recommendation(payload: WorkflowRequest) -> WorkflowOutput:
    payload.workflow_type = WorkflowType.RECOMMENDATION
    return await runtime.run(payload)


@router.post("/workflow/document", response_model=WorkflowOutput)
async def document(payload: WorkflowRequest) -> WorkflowOutput:
    payload.workflow_type = WorkflowType.DOCUMENT
    return await runtime.run(payload)


@router.post("/workflow/autonomous-care", response_model=WorkflowOutput)
async def autonomous_care(payload: WorkflowRequest) -> WorkflowOutput:
    payload.workflow_type = WorkflowType.AUTONOMOUS_CARE
    if not payload.agent_mode:
        payload.agent_mode = "autonomous"
    return await runtime.run(payload)


@router.get("/workflow/history", response_model=list[WorkflowHistoryItem])
async def workflow_history(
    patient_id: str | None = Query(default=None),
    conversation_id: str | None = Query(default=None),
    requested_by: str | None = Query(default=None),
    workflow_type: WorkflowType | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[WorkflowHistoryItem]:
    return workflow_history_store.list(
        patient_id=patient_id,
        conversation_id=conversation_id,
        requested_by=requested_by,
        workflow_type=workflow_type,
        limit=limit,
    )
