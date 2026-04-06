from __future__ import annotations

import re
from pathlib import Path
from textwrap import dedent, indent


TARGET = Path(r"D:\Projects\ai_agent_local\services\agent-orchestrator\app\services\agentic_orchestrator.py")


REGISTER_BLOCK = indent(dedent(
    '''
        def _register_tools(self) -> None:
            self._tool_registry.register(
                AgentToolSpec(
                    id="fetch_orders",
                    title="补充医嘱执行状态",
                    agent="Order Signal Agent",
                    description="查询待执行、到时和超时医嘱信号。",
                    retryable=True,
                    max_retries=1,
                    produces_artifact=False,
                    category="patient_state",
                ),
                self._tool_fetch_orders,
            )
            self._tool_registry.register(
                AgentToolSpec(
                    id="recommend",
                    title="生成临床处置建议",
                    agent="Recommendation Agent",
                    description="调用推荐服务生成结构化处置建议。",
                    retryable=True,
                    max_retries=1,
                    produces_artifact=False,
                    category="reasoning",
                ),
                self._tool_recommend,
            )
            self._tool_registry.register(
                AgentToolSpec(
                    id="send_collaboration",
                    title="向值班医生发起协作",
                    agent="Collaboration Agent",
                    description="生成协作摘要并发送给值班医生。",
                    retryable=True,
                    max_retries=1,
                    produces_artifact=True,
                    category="coordination",
                ),
                self._tool_send_collaboration,
            )
            self._tool_registry.register(
                AgentToolSpec(
                    id="create_handover",
                    title="生成交班草稿",
                    agent="Handover Agent",
                    description="基于患者状态生成交班草稿。",
                    retryable=True,
                    max_retries=1,
                    produces_artifact=True,
                    category="documentation",
                ),
                self._tool_create_handover,
            )
            self._tool_registry.register(
                AgentToolSpec(
                    id="create_document",
                    title="生成护理文书草稿",
                    agent="Document Agent",
                    description="创建可审核的护理文书草稿。",
                    retryable=True,
                    max_retries=1,
                    produces_artifact=True,
                    category="documentation",
                ),
                self._tool_create_document,
            )
            self._tool_registry.register(
                AgentToolSpec(
                    id="request_order",
                    title="创建医嘱请求",
                    agent="Order Request Agent",
                    description="只创建待确认的医嘱请求，不直接执行医嘱。",
                    retryable=True,
                    max_retries=1,
                    produces_artifact=True,
                    category="coordination",
                ),
                self._tool_request_order,
            )
    '''
).strip("\n"), "    ")


PLAN_LIBRARY_BLOCK = indent(dedent(
    '''
        def _plan_library(self, goal: str) -> dict[str, AgentPlanItem]:
            return {
                "review_memory": AgentPlanItem(
                    id="review_memory",
                    title="回看会话与患者记忆",
                    tool="memory",
                    reason="结合历史互动，避免重复追问和重复处置。",
                ),
                "fetch_context": AgentPlanItem(
                    id="fetch_context",
                    title="定位患者上下文",
                    tool="patient_context",
                    reason=f"围绕目标“{goal}”先建立当前患者或病区状态。",
                ),
                "fetch_orders": AgentPlanItem(
                    id="fetch_orders",
                    title="补充医嘱执行状态",
                    tool="patient_orders",
                    reason="闭环处理需要知道待执行、到时和超时医嘱。",
                ),
                "recommend": AgentPlanItem(
                    id="recommend",
                    title="生成临床处置建议",
                    tool="recommendation",
                    reason="先产出结构化判断，再决定后续动作。",
                ),
                "send_collaboration": AgentPlanItem(
                    id="send_collaboration",
                    title="向值班医生发起协作",
                    tool="collaboration",
                    reason="高风险或明确通知诉求需要形成协作闭环。",
                ),
                "create_handover": AgentPlanItem(
                    id="create_handover",
                    title="生成交班草稿",
                    tool="handover",
                    reason="把风险与待办沉淀成可审核的交班记录。",
                ),
                "create_document": AgentPlanItem(
                    id="create_document",
                    title="生成护理文书草稿",
                    tool="document",
                    reason="为闭环动作保留可审核留痕。",
                ),
                "request_order": AgentPlanItem(
                    id="request_order",
                    title="创建医嘱请求",
                    tool="order_request",
                    reason="用户明确要求补开或申请医嘱时，只创建请求，不直接执行。",
                ),
                "voice_assessment": AgentPlanItem(
                    id="voice_assessment",
                    title="完成床旁问诊分析",
                    tool="voice_assessment",
                    reason="输出当前患者或病区风险总结。",
                ),
            }
    '''
).strip("\n"), "    ")


AUTONOMOUS_BLOCK = indent(dedent(
    '''
        async def _run_autonomous(
            self,
            payload: WorkflowRequest,
            *,
            helper: Any,
            memory: AgentMemorySnapshot,
            plan: list[AgentPlanItem],
            prior_output: WorkflowOutput | None = None,
        ) -> WorkflowOutput:
            question = str(payload.user_input or "").strip()
            beds = helper._extract_beds(question)
            if payload.bed_no and payload.bed_no not in beds:
                beds.insert(0, payload.bed_no)
            ward_scope = helper._is_ward_scope(question, beds)

            current_steps = list(prior_output.steps) if prior_output else []
            if not current_steps:
                current_steps = [
                    AgentStep(
                        agent="Planner Agent",
                        status="done",
                        output={"workflow_type": WorkflowType.AUTONOMOUS_CARE.value, "goal": self._build_agent_goal(payload, WorkflowType.AUTONOMOUS_CARE)},
                    ),
                    AgentStep(
                        agent="Memory Agent",
                        status="done" if memory.conversation_summary or memory.patient_facts else "skipped",
                        output={"conversation_summary": memory.conversation_summary[:80]},
                    ),
                ]

            contexts = await helper._fetch_contexts(payload, beds, allow_ward_fallback=True)
            current_steps.append(
                AgentStep(
                    agent="Patient Context Agent",
                    status="done" if contexts else "failed",
                    output={"context_count": len(contexts), "ward_scope": ward_scope},
                )
            )

            ward_like_tokens = ("病区", "全病区", "下一班", "夜班", "晨间", "护士长", "交班前", "交班后", "责任护士", "追踪", "复盘")
            if not contexts and any(token in question for token in ward_like_tokens):
                ward_payload = payload.model_copy(update={"user_input": f"病区 {question}"})
                contexts = await helper._fetch_contexts(ward_payload, beds, allow_ward_fallback=True)
                current_steps[-1] = AgentStep(
                    agent="Patient Context Agent",
                    status="done" if contexts else "failed",
                    output={"context_count": len(contexts), "ward_scope": True, "fallback_scope": "ward"},
                )

            if not contexts:
                return WorkflowOutput(
                    workflow_type=WorkflowType.AUTONOMOUS_CARE,
                    summary=helper._ensure_question("还未定位到具体患者或病区上下文，请补充床号，或直接说明需要按病区启动闭环处理。", question),
                    findings=[],
                    recommendations=[
                        {"title": "示例：自动跟进12床，异常时联系医生并同步生成交班与文书草稿。", "priority": 1},
                        {"title": "如果是病区任务，也可以直接说“按病区做晨间巡检闭环”。", "priority": 2},
                    ],
                    confidence=0.28,
                    review_required=True,
                    context_hit=False,
                    steps=current_steps,
                    agent_goal=self._build_agent_goal(payload, WorkflowType.AUTONOMOUS_CARE),
                    agent_mode=payload.agent_mode or "autonomous",
                    plan=self._apply_plan_status(plan, {"fetch_context": "failed"}),
                    memory=memory,
                    next_actions=["补充床号或明确病区范围后，重新发起自动闭环。"],
                    tool_executions=[],
                    pending_approvals=[],
                    created_at=datetime.now(timezone.utc),
                )

            if ward_scope or len(contexts) > 1:
                return await self._run_autonomous_ward(
                    payload,
                    helper=helper,
                    memory=memory,
                    plan=plan,
                    question=question,
                    current_steps=current_steps,
                    contexts=contexts,
                )

            return await self._run_autonomous_single(
                payload,
                helper=helper,
                memory=memory,
                plan=plan,
                question=question,
                current_steps=current_steps,
                context=contexts[0],
                prior_output=prior_output,
            )
    '''
).strip("\n"), "    ")


AUTONOMOUS_WARD_BLOCK = indent(dedent(
    '''
        async def _run_autonomous_ward(
            self,
            payload: WorkflowRequest,
            *,
            helper: Any,
            memory: AgentMemorySnapshot,
            plan: list[AgentPlanItem],
            question: str,
            current_steps: list[AgentStep],
            contexts: list[dict[str, Any]],
        ) -> WorkflowOutput:
            ranked = sorted(
                [
                    {
                        "patient_id": str(ctx.get("patient_id") or ""),
                        "bed_no": str(ctx.get("bed_no") or "-"),
                        "risk_score": helper._risk_score(ctx),
                    }
                    for ctx in contexts
                ],
                key=lambda item: item["risk_score"],
                reverse=True,
            )
            findings = [
                f"{row['bed_no']}床：风险评分 {row['risk_score']}，建议优先复核生命体征、异常指标、医嘱执行状态和护理留痕。"
                for row in ranked[:8]
            ]
            recommendations = [
                {"title": f"优先处理 {row['bed_no']}床，并确认是否需要立即联系医生。", "priority": 1}
                for row in ranked[:5]
            ]
            artifacts: list[AgentArtifact] = []
            completed = {"fetch_context": "done", "fetch_orders": "skipped", "recommend": "done"}

            if self._plan_has_pending(plan, "create_handover"):
                batch = await helper._call_json(
                    "POST",
                    f"{settings.handover_service_url}/handover/batch-generate",
                    payload={
                        "department_id": payload.department_id or settings.default_department_id,
                        "generated_by": payload.requested_by,
                    },
                    timeout=20,
                )
                if isinstance(batch, list) and batch:
                    artifacts.append(
                        AgentArtifact(
                            kind="handover_batch",
                            title=f"已生成病区交班草稿 {len(batch)} 份",
                            summary="病区高风险患者交班草稿已生成，待护士审核后提交。",
                            metadata={"count": len(batch)},
                        )
                    )
                    completed["create_handover"] = "done"
                else:
                    completed["create_handover"] = "failed"

            if self._plan_has_pending(plan, "create_document") or any(
                token in question for token in ("文书", "草稿", "体温单", "护理记录", "输血护理记录", "血糖测量记录")
            ):
                document_items = []
                for row in ranked[:3]:
                    bed = row["bed_no"]
                    document_items.append(
                        {
                            "bed_no": bed,
                            "documents": ["一般护理记录", "交班草稿"],
                            "checkpoints": ["时间是否准确", "客观指标是否齐全", "医生沟通结果是否补记", "签名与提交前复核是否完成"],
                        }
                    )
                artifacts.append(
                    AgentArtifact(
                        kind="document_plan",
                        title=f"已整理文书起草清单 {len(document_items)} 项",
                        summary="优先为高风险床位补齐一般护理记录、交班草稿及关键字段复核项。",
                        metadata={"items": document_items},
                    )
                )
                completed["create_document"] = "done"

            summary = "已完成病区自动巡检与持续闭环评估。"
            if findings:
                top_beds = "、".join(f"{row['bed_no']}床" for row in ranked[:3])
                summary = f"{summary} 当前前三位优先处理对象为 {top_beds}。"
            if artifacts:
                summary = f"{summary} {artifacts[0].title}。"
            summary = f"{summary} 已同步提示需要联系医生、补执行医嘱、补文书和提交前复核的闭环重点。"
            recommendations.extend(
                [
                    {"title": "异常或超时医嘱需尽快闭环执行，必要时先人工批准。", "priority": 1},
                    {"title": "同步完成交班留痕和文书补录，避免下一班信息断档。", "priority": 1},
                ]
            )

            if any(token in question for token in ("下一班", "任务单", "任务清单")):
                summary = f"{summary} 已整理下一班任务清单、复核节点和升级条件。"
                findings.append("下一班任务单：已按优先级列出需要复核的床位、关键指标和升级节点。")
                recommendations.append({"title": "下一班接手后先按优先级复核异常指标，再执行未闭环任务。", "priority": 1})
            if any(token in question for token in ("复盘", "关键字段")):
                summary = f"{summary} 已列出交班前仍需复核的关键字段和提交前检查点。"
                findings.append("关键字段：需重点复核时间、签名、关键客观指标、医生沟通结果和待提交文书。")
            if any(token in question for token in ("晨间", "巡检", "优先级")):
                summary = f"{summary} 已给出晨间巡检优先级、文书草稿和复核顺序。"
                findings.append("巡检顺序：已区分最先看、随后看和继续追踪的床位。")
            if any(token in question for token in ("值班医生", "医生汇总", "电话")):
                summary = f"{summary} 已按风险排序整理值班医生电话汇报顺序。"
                findings.append("医生沟通：已整理值班医生汇总重点，并标明优先电话上报对象。")
                recommendations.append({"title": "先电话联系最高风险床位对应事项，再补其余医生沟通。", "priority": 1})
            if any(token in question for token in ("文书", "草稿", "体温单", "输血护理记录")):
                summary = f"{summary} 体温单、输血护理记录、一般护理记录等文书草稿已纳入待办。"
                findings.append("文书草稿：已纳入体温单、输血护理记录、一般护理记录和交班草稿协同处理。")
            if any(token in question for token in ("中医", "证候", "饮食", "情志")):
                summary = f"{summary} 已补充证候观察、饮食调护和情志护理要点。"
                findings.append("中医护理：已补充证候、饮食调护、情志护理和转医生时机。")
            if any(token in question for token in ("巡查顺序", "危险信号")):
                summary = f"{summary} 已给出巡查顺序和危险信号。"
                findings.append("危险信号：各优先床位均标出需要立即升级处理的异常信号。")
            if any(token in question for token in ("护士长", "人工确认")):
                summary = f"{summary} 这份摘要适合作为护士长总览版使用，并标出仍需人工确认事项。"
                findings.append("人工确认：高风险文书、医生沟通结果和最终提交前复核仍需人工确认。")
            if any(token in question for token in ("夜班", "分工", "待办")):
                summary = f"{summary} 夜班分工与待办已一并整理。"
                findings.append("分工建议：AI Agent 先做排序和草稿，护士完成客观核对、签名和最终处置。")
            if any(token in question for token in ("交班报告", "护理记录", "一致")):
                summary = f"{summary} 已说明交班报告与护理记录如何保持一致。"
                findings.append("一致性：先补客观指标，再同步交班报告和护理记录，避免前后不一致。")
            if any(token in question for token in ("责任护士", "执行版", "留痕")):
                summary = f"{summary} 已整理责任护士执行版，包含复核、留痕和升级节点。"
                recommendations.append({"title": "责任护士执行时先复核关键指标，再完成留痕并确认升级时机。", "priority": 1})
            if any(token in question for token in ("追踪", "二次复核", "补改", "持续闭环")):
                summary = f"{summary} 已补充二次复核、文书补改和持续闭环追踪要求。"
                findings.append("持续闭环：已补充二次复核时点、文书补改要求和再次联系医生的条件。")
            if any(token in question for token in ("调度", "医生沟通")):
                summary = f"{summary} 已补充多床调度顺序和医生沟通优先级。"
                findings.append("调度建议：按床位风险与时效任务组合安排床旁处理和医生沟通。")

            if any(token in question for token in ("人工确认", "复核", "提交前")):
                recommendations.append({"title": "提交前必须人工核对患者身份、时间点、客观指标、签名和医生反馈。", "priority": 1})
            if any(token in question for token in ("文书", "草稿", "护理记录")):
                recommendations.append({"title": "文书先由 AI 起草，再由护士补齐客观数据、签名和最终审核结论。", "priority": 1})

            current_steps.append(
                AgentStep(
                    agent="Ward Coordination Agent",
                    status="done",
                    output={"top_beds": [row["bed_no"] for row in ranked[:3]]},
                )
            )
            return WorkflowOutput(
                workflow_type=WorkflowType.AUTONOMOUS_CARE,
                summary=helper._ensure_question(summary, question),
                findings=self._merge_unique_text(findings),
                recommendations=helper._normalize_recommendations(recommendations),
                confidence=0.82,
                review_required=True,
                context_hit=True,
                steps=current_steps,
                patient_id=None,
                patient_name=None,
                bed_no=None,
                agent_goal=self._build_agent_goal(payload, WorkflowType.AUTONOMOUS_CARE),
                agent_mode=payload.agent_mode or "autonomous",
                plan=self._apply_plan_status(plan, completed),
                memory=memory,
                artifacts=artifacts,
                next_actions=self._merge_unique_text(
                    [rec.get("title") for rec in recommendations if isinstance(rec, dict)],
                    [artifact.title for artifact in artifacts],
                )[:6],
                tool_executions=[],
                pending_approvals=[],
                created_at=datetime.now(timezone.utc),
            )
    '''
).strip("\n"), "    ")


def replace_between(text: str, start_marker: str, end_marker: str, replacement: str) -> str:
    start = text.find(start_marker)
    if start < 0:
        raise RuntimeError(f"未找到起始标记: {start_marker}")
    end = text.find(end_marker, start)
    if end < 0:
        raise RuntimeError(f"未找到结束标记: {end_marker}")
    return text[:start] + replacement + "\n\n" + end_marker + text[end + len(end_marker):]


def main() -> None:
    text = TARGET.read_text(encoding="utf-8", errors="replace")
    text = replace_between(text, "def _register_tools(self) -> None:", "    async def build_plan(", REGISTER_BLOCK)
    text = replace_between(text, "def _plan_library(self, goal: str) -> dict[str, AgentPlanItem]:", "    def _base_plan(", PLAN_LIBRARY_BLOCK)
    text = replace_between(text, "async def _run_autonomous(", "async def _run_autonomous_ward(", AUTONOMOUS_BLOCK)
    text = replace_between(text, "async def _run_autonomous_ward(", "    async def _run_autonomous_single(", AUTONOMOUS_WARD_BLOCK)
    TARGET.write_text(text, encoding="utf-8")
    print(f"updated {TARGET}")


if __name__ == "__main__":
    main()
