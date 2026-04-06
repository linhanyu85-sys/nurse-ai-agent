from __future__ import annotations

import asyncio
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.schemas.workflow import (  # noqa: E402
    AgentApprovalRequest,
    AgentArtifact,
    AgentMemorySnapshot,
    AgentPlanItem,
    WorkflowOutput,
    WorkflowRequest,
    WorkflowType,
)
from app.services.agentic_orchestrator import agentic_orchestrator, is_autonomous_request  # noqa: E402
from app.services.state_machine import AgentStateMachine  # noqa: E402


class AgenticRuntimeTests(unittest.TestCase):
    @staticmethod
    def _build_context(*, bed_no: str, patient_id: str, patient_name: str) -> dict:
        return {
            "bed_no": bed_no,
            "patient_id": patient_id,
            "patient_name": patient_name,
        }

    def _run_document_with_contexts(self, user_input: str, contexts: list[dict]) -> WorkflowOutput:
        engine = AgentStateMachine()

        async def fake_fetch_contexts(*args, **kwargs):  # noqa: ANN002, ANN003
            return contexts

        engine._fetch_contexts = fake_fetch_contexts  # type: ignore[method-assign]
        payload = WorkflowRequest(
            workflow_type=WorkflowType.DOCUMENT,
            user_input=user_input,
            requested_by="u_test",
            department_id="dep-card-01",
        )
        return asyncio.run(engine._run_document(payload))

    def _run_handover_with_contexts(self, user_input: str, contexts: list[dict]) -> WorkflowOutput:
        engine = AgentStateMachine()

        async def fake_fetch_contexts(*args, **kwargs):  # noqa: ANN002, ANN003
            return contexts

        engine._fetch_contexts = fake_fetch_contexts  # type: ignore[method-assign]
        payload = WorkflowRequest(
            workflow_type=WorkflowType.HANDOVER,
            user_input=user_input,
            requested_by="u_test",
            department_id="dep-card-01",
        )
        return asyncio.run(engine._run_handover(payload))

    def _run_recommendation_with_contexts(self, user_input: str, contexts: list[dict]) -> WorkflowOutput:
        engine = AgentStateMachine()

        async def fake_fetch_contexts(*args, **kwargs):  # noqa: ANN002, ANN003
            return contexts

        engine._fetch_contexts = fake_fetch_contexts  # type: ignore[method-assign]
        payload = WorkflowRequest(
            workflow_type=WorkflowType.RECOMMENDATION,
            user_input=user_input,
            requested_by="u_test",
            department_id="dep-card-01",
        )
        return asyncio.run(engine._run_recommendation(payload))

    def test_autonomous_request_detection(self) -> None:
        self.assertTrue(is_autonomous_request("autonomous follow up bed 12 and notify doctor"))
        self.assertTrue(is_autonomous_request("agent please notify doctor and create document"))
        self.assertFalse(is_autonomous_request("show me bed 12 status"))

    def test_build_plan_for_autonomous_care_contains_closure_tools(self) -> None:
        payload = WorkflowRequest(
            workflow_type=WorkflowType.AUTONOMOUS_CARE,
            patient_id="p-001",
            user_input="autonomous follow up bed 12, notify doctor, handover, and document it",
            requested_by="u_linmeili",
        )
        memory = AgentMemorySnapshot()
        plan = asyncio.run(agentic_orchestrator.build_plan(payload, WorkflowType.AUTONOMOUS_CARE, memory))
        ids = {item.id for item in plan}
        self.assertIn("fetch_context", ids)
        self.assertIn("fetch_orders", ids)
        self.assertIn("recommend", ids)
        self.assertIn("send_collaboration", ids)
        self.assertIn("create_handover", ids)
        self.assertIn("create_document", ids)

    def test_full_loop_execution_profile_forces_closure_actions(self) -> None:
        payload = WorkflowRequest(
            workflow_type=WorkflowType.AUTONOMOUS_CARE,
            patient_id="p-001",
            user_input="follow up bed 12",
            requested_by="u_linmeili",
            execution_profile="full_loop",
        )
        memory = AgentMemorySnapshot()
        plan = asyncio.run(agentic_orchestrator.build_plan(payload, WorkflowType.AUTONOMOUS_CARE, memory))
        ids = {item.id for item in plan}
        self.assertIn("send_collaboration", ids)
        self.assertIn("create_handover", ids)
        self.assertIn("create_document", ids)

    def test_execution_profile_can_coerce_workflow_route(self) -> None:
        async def fallback_route(_: str) -> WorkflowType:
            return WorkflowType.VOICE_INQUIRY

        payload = WorkflowRequest(
            workflow_type=WorkflowType.VOICE_INQUIRY,
            patient_id="p-001",
            user_input="show me bed 12 status",
            execution_profile="document",
        )
        routed = asyncio.run(agentic_orchestrator.route_workflow(payload, fallback_route))
        self.assertEqual(routed, WorkflowType.DOCUMENT)

    def test_planning_brief_can_trigger_autonomous_route(self) -> None:
        async def fallback_route(_: str) -> WorkflowType:
            return WorkflowType.VOICE_INQUIRY

        payload = WorkflowRequest(
            workflow_type=WorkflowType.VOICE_INQUIRY,
            patient_id="p-001",
            user_input="",
            mission_title="夜班自动跟进",
            success_criteria=["通知医生", "生成交班草稿"],
        )
        routed = asyncio.run(agentic_orchestrator.route_workflow(payload, fallback_route))
        self.assertEqual(routed, WorkflowType.AUTONOMOUS_CARE)

    def test_route_workflow_prefers_fallback_for_ai_agent_scaffold_prompt(self) -> None:
        async def fallback_route(_: str) -> WorkflowType:
            return WorkflowType.RECOMMENDATION

        payload = WorkflowRequest(
            workflow_type=WorkflowType.VOICE_INQUIRY,
            user_input=(
                "请把下面内容当成护士在真实临床班次里一次性交给 AI Agent 的长任务，不要把它当成普通聊天。"
                "如果涉及一般临床问题，请像带教老师一样直接作答，不要强行要求补床号。"
                "请说明低血压少尿时床旁先评估什么、何时联系医生。"
            ),
            mission_title="夜班闭环跟进",
            success_criteria=["通知医生", "生成交班草稿"],
        )
        routed = asyncio.run(agentic_orchestrator.route_workflow(payload, fallback_route))
        self.assertEqual(routed, WorkflowType.RECOMMENDATION)

    def test_autonomous_request_ignores_scaffolded_ai_agent_wording(self) -> None:
        prompt = (
            "请把下面内容当成护士在真实临床班次里一次性交给 AI Agent 的长任务，不要把它当成普通聊天。"
            "如果涉及一般临床问题，请像带教老师一样直接作答，不要强行要求补床号。"
            "请说明疼痛干预后15-30分钟和1-2小时的复评时点。"
        )
        self.assertFalse(is_autonomous_request(prompt))

    def test_system_design_query_ignores_visual_scaffold_in_clinical_prompt(self) -> None:
        engine = AgentStateMachine()
        prompt = (
            "请把下面内容当成护士在真实临床班次里一次性交给 AI Agent 的长任务。"
            "请不要只给概念性建议，而要把病区风险热力图、今日待办时间轴、交接班摘要看板之间的关系说清楚。"
            "现在真正要回答的是：低血压少尿时床旁先评估什么、何时联系医生。"
        )
        self.assertFalse(engine._is_system_design_query(prompt))

    def test_system_design_query_accepts_visual_design_prompt(self) -> None:
        engine = AgentStateMachine()
        prompt = (
            "请解释病区风险热力图、今日待办时间轴和交接班摘要看板各自解决什么临床问题，"
            "并说明护士看完后应该立刻采取什么动作。"
        )
        self.assertTrue(engine._is_system_design_query(prompt))

    def test_route_intent_uses_recommendation_for_no_patient_clinical_prompt(self) -> None:
        engine = AgentStateMachine()
        prompt = (
            "请把下面内容当成护士在真实临床班次里一次性交给 AI Agent 的长任务。"
            "如果涉及一般临床问题，请像带教老师一样直接作答，不要强行要求补床号。"
            "现在要回答的是：低血压少尿时床旁先评估什么、何时联系医生、下一班交接班怎么写重点。"
        )
        routed = asyncio.run(engine.route_intent(prompt))
        self.assertEqual(routed, WorkflowType.RECOMMENDATION)

    def test_explicit_no_patient_query_recognizes_single_patient_wording(self) -> None:
        engine = AgentStateMachine()
        prompt = "不针对单一患者，只想问低血压和低氧时护士怎么沟通、何时联系医生，不要补床号。"
        self.assertTrue(engine._is_explicit_no_patient_query(prompt))

    def test_explicit_no_patient_query_recognizes_specific_patient_wording(self) -> None:
        engine = AgentStateMachine()
        prompt = "不针对具体患者，只想问胸闷胸痛时护士床旁先做什么。"
        self.assertTrue(engine._is_explicit_no_patient_query(prompt))

    def test_route_intent_prefers_recommendation_for_family_communication_prompt(self) -> None:
        engine = AgentStateMachine()
        prompt = (
            "不针对单一患者，聚焦家属担心低血压、低氧、输血反应和术后恢复时如何沟通。"
            "请给出护士可直接使用的沟通框架，说明哪些情况要告知并联系医生、哪些内容要留痕，"
            "最后补一句适合交接班提醒同事的沟通注意点。"
        )
        routed = asyncio.run(engine.route_intent(prompt))
        self.assertEqual(routed, WorkflowType.RECOMMENDATION)

    def test_run_handover_falls_back_to_general_family_communication_answer(self) -> None:
        output = self._run_handover_with_contexts(
            "不针对单一患者，只想问家属沟通时怎样解释低血压、低氧和输血反应风险，"
            "哪些情况需要告知并联系医生、哪些内容要留痕，并补一句交接班提醒。",
            [],
        )
        text = "\n".join([output.summary, *output.findings, *(item["title"] for item in output.recommendations)])
        self.assertEqual(output.workflow_type, WorkflowType.RECOMMENDATION)
        for keyword in ("沟通", "告知", "留痕", "联系医生", "交接班"):
            self.assertIn(keyword, text)

    def test_run_document_returns_glucose_wound_handover_guidance(self) -> None:
        output = self._run_document_with_contexts(
            "请按血糖谱记录单思路列出今日还应补录的时点与字段，说明怎样把餐前、随机血糖、复测、POCT、伤口观察写进护理记录和交接班。",
            [self._build_context(bed_no="16", patient_id="pat-003", patient_name="李建国")],
        )
        text = "\n".join([output.summary, *output.findings, *(item["title"] for item in output.recommendations)])
        for keyword in ("餐前", "随机血糖", "复测", "POCT", "交接班", "伤口"):
            self.assertIn(keyword, text)
        self.assertTrue(output.context_hit)

    def test_run_document_returns_surgical_count_guidance_with_context(self) -> None:
        output = self._run_document_with_contexts(
            "请按手术开始前、关闭体腔前、关闭体腔后、缝合皮肤后四个节点整理手术物品清点记录，强调双人逐项清点、同步唱点和签名。",
            [self._build_context(bed_no="17", patient_id="pat-004", patient_name="赵敏")],
        )
        text = "\n".join([output.summary, *output.findings, *(item["title"] for item in output.recommendations)])
        for keyword in ("双人逐项清点", "关闭体腔前", "关闭体腔后", "缝合皮肤后", "签名"):
            self.assertIn(keyword, text)
        self.assertTrue(output.context_hit)

    def test_run_document_returns_archive_flow_guidance_with_contexts(self) -> None:
        output = self._run_document_with_contexts(
            "请按草稿区、待审核、待提交、已归档四段说明文书流转，并说明护士、审核者和系统如何把文书归到患者档案，且保留人工复核。",
            [
                self._build_context(bed_no="12", patient_id="pat-001", patient_name="张晓明"),
                self._build_context(bed_no="23", patient_id="pat-002", patient_name="王秀兰"),
            ],
        )
        text = "\n".join([output.summary, *output.findings, *(item["title"] for item in output.recommendations)])
        for keyword in ("草稿区", "待审核", "待提交", "已归档", "患者档案", "人工复核"):
            self.assertIn(keyword, text)
        self.assertTrue(output.context_hit)

    def test_run_document_returns_template_import_validation_guidance(self) -> None:
        output = self._run_document_with_contexts(
            "说明模板导入后 AI 如何识别字段、自动回填、标记待补字段，以及提交前校验和人工复核如何配合。",
            [self._build_context(bed_no="12", patient_id="pat-001", patient_name="张晓明")],
        )
        text = "\n".join([output.summary, *output.findings, *(item["title"] for item in output.recommendations)])
        for keyword in ("模板导入", "待补字段", "自动回填", "提交前校验", "人工复核"):
            self.assertIn(keyword, text)
        self.assertTrue(output.context_hit)

    def test_run_document_prefers_archive_flow_over_listed_document_examples(self) -> None:
        output = self._run_document_with_contexts(
            "请按草稿区、待审核、待提交、已归档四段说明文书流转。"
            "背景里会出现体温单、病重护理记录、输血护理记录和血糖记录草稿，但这次重点是页面状态、患者档案和人工复核。",
            [
                self._build_context(bed_no="12", patient_id="pat-001", patient_name="张晓明"),
                self._build_context(bed_no="23", patient_id="pat-002", patient_name="刘娜"),
            ],
        )
        text = "\n".join([output.summary, *output.findings, *(item["title"] for item in output.recommendations)])
        for keyword in ("草稿区", "待审核", "待提交", "已归档", "患者档案", "人工复核"):
            self.assertIn(keyword, text)
        self.assertNotIn("输血前 → 开始后15分钟", text)

    def test_run_document_prefers_template_flow_over_listed_document_examples(self) -> None:
        output = self._run_document_with_contexts(
            "护理部已整理体温单、病重护理记录、输血记录、血糖记录和交接班模板。"
            "这次重点是模板导入、自动回填、待补字段、提交前校验和人工复核，不是展开某一份输血记录。",
            [
                self._build_context(bed_no="12", patient_id="pat-001", patient_name="张晓明"),
                self._build_context(bed_no="16", patient_id="pat-003", patient_name="李建国"),
            ],
        )
        text = "\n".join([output.summary, *output.findings, *(item["title"] for item in output.recommendations)])
        for keyword in ("模板导入", "待补字段", "自动回填", "提交前校验", "人工复核"):
            self.assertIn(keyword, text)
        self.assertNotIn("输血前 → 开始后15分钟", text)

    def test_run_document_returns_multi_document_priority_guidance(self) -> None:
        output = self._run_document_with_contexts(
            "请按优先级排序 12 床今天要补的体温单、病重护理记录和交接班，并说明审核归档的闭环。",
            [self._build_context(bed_no="12", patient_id="pat-001", patient_name="张晓明")],
        )
        text = "\n".join([output.summary, *output.findings, *(item["title"] for item in output.recommendations)])
        for keyword in ("体温单", "病重护理记录", "交接班", "优先级", "归档"):
            self.assertIn(keyword, text)
        self.assertTrue(output.context_hit)

    def test_run_document_returns_patient_archive_search_guidance(self) -> None:
        output = self._run_document_with_contexts(
            "请描述患者档案页怎样展示草稿、已归档和状态，并说明搜索栏应支持哪些文书类型关键词命中。",
            [
                self._build_context(bed_no="12", patient_id="pat-001", patient_name="张晓明"),
                self._build_context(bed_no="16", patient_id="pat-003", patient_name="李建国"),
            ],
        )
        text = "\n".join([output.summary, *output.findings, *(item["title"] for item in output.recommendations)])
        for keyword in ("患者档案", "草稿", "已归档", "搜索", "文书类型", "状态"):
            self.assertIn(keyword, text)
        self.assertTrue(output.context_hit)

    def test_run_handover_returns_ordered_ward_summary(self) -> None:
        output = self._run_handover_with_contexts(
            "白班准备生成今日护理交接班报告，请按规范顺序把出科、入科、病重病危、当日手术、次日手术、高危患者和异常事件同步到今日待办。",
            [
                self._build_context(bed_no="12", patient_id="pat-001", patient_name="张晓明"),
                self._build_context(bed_no="18", patient_id="pat-005", patient_name="陈月"),
                self._build_context(bed_no="20", patient_id="pat-006", patient_name="周阿姨"),
            ],
        )
        text = "\n".join([output.summary, *output.findings, *(item["title"] for item in output.recommendations)])
        for keyword in ("出科", "入科", "病重病危", "当日手术", "次日手术", "高危患者", "异常事件", "今日待办"):
            self.assertIn(keyword, text)
        self.assertTrue(output.context_hit)

    def test_answer_general_question_keeps_pressure_ulcer_guidance_ahead_of_generic_communication(self) -> None:
        engine = AgentStateMachine()
        answer = asyncio.run(
            engine._answer_general_question(
                "护士长让系统整理压伤高风险患者今日护理重点，"
                "回答里会提到病区风险、护理观察和医生沟通，但重点是翻身、皮肤观察、营养、交接班和护理记录。"
            )
        )
        for keyword in ("翻身", "皮肤观察", "营养", "交接班", "护理记录"):
            self.assertIn(keyword, answer)

    def test_build_agent_goal_includes_mission_context(self) -> None:
        payload = WorkflowRequest(
            workflow_type=WorkflowType.RECOMMENDATION,
            patient_id="p-001",
            user_input="review bed 12",
            mission_title="梳理升级风险",
            success_criteria=["明确是否需要上报", "给出下一步观察重点"],
            execution_profile="escalate",
        )
        goal = agentic_orchestrator._build_agent_goal(payload, WorkflowType.RECOMMENDATION)
        self.assertIn("梳理升级风险", goal)
        self.assertIn("明确是否需要上报", goal)

    def test_reflect_adds_collaboration_when_output_requests_escalation(self) -> None:
        payload = WorkflowRequest(
            workflow_type=WorkflowType.AUTONOMOUS_CARE,
            patient_id="p-001",
            user_input="autonomous follow up bed 12",
            requested_by="u_linmeili",
        )
        output = WorkflowOutput(
            workflow_type=WorkflowType.AUTONOMOUS_CARE,
            summary="Bed 12 has overdue orders and should be escalated.",
            findings=["There are 2 overdue orders.", "Escalation is recommended."],
            recommendations=[{"title": "Immediately notify doctor on duty", "priority": 1}],
            confidence=0.86,
            review_required=True,
            context_hit=True,
            patient_id="p-001",
            patient_name="Test Patient",
            bed_no="12",
            artifacts=[],
            created_at=datetime.now(timezone.utc),
        )
        critique = agentic_orchestrator.reflect(payload, output)
        followup_ids = {item.id for item in critique["followup_actions"]}
        self.assertIn("send_collaboration", followup_ids)

    def test_reflect_stops_when_waiting_for_approval(self) -> None:
        payload = WorkflowRequest(
            workflow_type=WorkflowType.AUTONOMOUS_CARE,
            patient_id="p-001",
            user_input="autonomous follow up bed 12",
            requested_by="u_linmeili",
        )
        output = WorkflowOutput(
            workflow_type=WorkflowType.AUTONOMOUS_CARE,
            summary="Waiting for approval.",
            findings=[],
            recommendations=[],
            confidence=0.8,
            review_required=True,
            context_hit=True,
            patient_id="p-001",
            bed_no="12",
            pending_approvals=[
                AgentApprovalRequest(
                    id="approval-1",
                    item_id="send_collaboration",
                    tool_id="collaboration",
                    title="Notify doctor",
                    created_at=datetime.now(timezone.utc),
                )
            ],
            created_at=datetime.now(timezone.utc),
        )
        critique = agentic_orchestrator.reflect(payload, output)
        self.assertEqual(critique["reason"], "awaiting_approval")
        self.assertEqual(critique["followup_actions"], [])

    def test_default_next_actions_prefers_recommendations(self) -> None:
        output = WorkflowOutput(
            workflow_type=WorkflowType.RECOMMENDATION,
            summary="done",
            findings=[],
            recommendations=[
                {"title": "Prioritize bed 12", "priority": 1},
                {"title": "Review vitals", "priority": 1},
            ],
            confidence=0.8,
            review_required=True,
            context_hit=True,
            patient_id="p-001",
            patient_name="Test Patient",
            bed_no="12",
            artifacts=[AgentArtifact(kind="document_draft", title="Draft created")],
            created_at=datetime.now(timezone.utc),
        )
        next_actions = agentic_orchestrator._default_next_actions(output)
        self.assertEqual(next_actions[0], "Prioritize bed 12")

    def test_execute_autonomous_plan_requires_approval_for_sensitive_tool(self) -> None:
        state = {
            "patient_id": "p-001",
            "bed_no": "12",
            "patient_name": "Test Patient",
            "findings": [],
            "recommendations": [],
            "artifacts": [],
            "confidence": 0.7,
            "orders": None,
            "completed": {"send_collaboration": "pending"},
            "tool_steps": [],
            "tool_executions": [],
            "pending_approvals": [],
        }
        plan = [
            AgentPlanItem(
                id="send_collaboration",
                title="Notify doctor",
                tool="collaboration",
                reason="High risk signal requires escalation",
            )
        ]

        asyncio.run(
            agentic_orchestrator._execute_autonomous_plan(
                helper=object(),
                payload=WorkflowRequest(workflow_type=WorkflowType.AUTONOMOUS_CARE, patient_id="p-001"),
                question="autonomous follow up bed 12",
                plan=plan,
                state=state,
            )
        )

        self.assertEqual(state["completed"]["send_collaboration"], "approval_required")
        self.assertEqual(len(state["pending_approvals"]), 1)
        self.assertEqual(len(state["tool_executions"]), 0)
        self.assertEqual(state["tool_steps"][0].status, "approval_required")

    def test_execute_autonomous_plan_retries_retryable_tool(self) -> None:
        registered = agentic_orchestrator._tool_registry.get("recommend")
        self.assertIsNotNone(registered)
        assert registered is not None

        original_handler = registered.handler
        attempts = {"count": 0}

        async def flaky_recommend(**kwargs):
            attempts["count"] += 1
            if attempts["count"] == 1:
                return "failed", {"error": "temporary_failure"}
            return "done", {"recommendation_count": 1}

        agentic_orchestrator._tool_registry.register(registered.spec, flaky_recommend)
        try:
            state = {
                "patient_id": "p-001",
                "bed_no": "12",
                "patient_name": "Test Patient",
                "findings": [],
                "recommendations": [],
                "artifacts": [],
                "confidence": 0.7,
                "orders": None,
                "completed": {"recommend": "pending"},
                "tool_steps": [],
                "tool_executions": [],
                "pending_approvals": [],
            }
            plan = [AgentPlanItem(id="recommend", title="Generate recommendation", tool="recommendation")]

            asyncio.run(
                agentic_orchestrator._execute_autonomous_plan(
                    helper=object(),
                    payload=WorkflowRequest(workflow_type=WorkflowType.AUTONOMOUS_CARE, patient_id="p-001"),
                    question="auto follow up bed 12",
                    plan=plan,
                    state=state,
                )
            )
        finally:
            agentic_orchestrator._tool_registry.register(registered.spec, original_handler)

        self.assertEqual(state["completed"]["recommend"], "done")
        self.assertEqual(len(state["tool_executions"]), 1)
        self.assertEqual(state["tool_executions"][0].attempts, 2)
        self.assertEqual(state["tool_steps"][0].output["attempts"], 2)

    def test_finalize_builds_structured_role_and_reasoning_views(self) -> None:
        payload = WorkflowRequest(
            workflow_type=WorkflowType.AUTONOMOUS_CARE,
            patient_id="p-001",
            bed_no="12",
            user_input="请持续跟进12床低血压、通知医生并留痕",
            mission_title="夜班风险闭环",
            success_criteria=["完成风险扫描", "准备协作摘要", "沉淀护理记录"],
            execution_profile="full_loop",
        )
        output = WorkflowOutput(
            workflow_type=WorkflowType.AUTONOMOUS_CARE,
            summary="12床存在低血压与少尿信号，建议先复测血压并准备协作。",
            findings=["低血压风险", "尿量减少", "需要人工复核"],
            recommendations=[
                {"title": "立即复测血压并校验趋势", "priority": 1},
                {"title": "准备协作摘要并通知医生", "priority": 1},
                {"title": "同步生成护理记录草稿", "priority": 2},
            ],
            confidence=0.84,
            review_required=True,
            context_hit=True,
            patient_id="p-001",
            patient_name="Test Patient",
            bed_no="12",
            artifacts=[
                AgentArtifact(kind="document_draft", title="12床护理记录草稿"),
            ],
            pending_approvals=[
                AgentApprovalRequest(
                    id="approval-structured",
                    item_id="send_collaboration",
                    tool_id="collaboration",
                    title="通知值班医生",
                    created_at=datetime.now(timezone.utc),
                )
            ],
            created_at=datetime.now(timezone.utc),
        )

        finalized = agentic_orchestrator.finalize(payload, output)

        self.assertGreaterEqual(len(finalized.specialist_profiles), 2)
        self.assertTrue(any(item.id == "human_gate" for item in finalized.hybrid_care_path))
        self.assertIsNotNone(finalized.data_capsule)
        self.assertIsNotNone(finalized.health_graph)
        self.assertGreaterEqual(len(finalized.reasoning_cards), 3)

    def test_answer_general_question_handles_chest_pain_as_high_risk(self) -> None:
        engine = AgentStateMachine()
        answer = asyncio.run(engine._answer_general_question("胸闷胸痛时护士床旁先做什么，什么情况要马上联系医生"))
        for keyword in ("胸闷胸痛", "血压", "血氧", "联系医生"):
            self.assertIn(keyword, answer)

    def test_run_recommendation_handles_single_patient_tcm_chest_pain(self) -> None:
        output = self._run_recommendation_with_contexts(
            "12床胸闷胸痛时怎么做中医护理，哪些情况必须马上联系医生",
            [self._build_context(bed_no="12", patient_id="pat-001", patient_name="张晓明")],
        )
        text = "\n".join([output.summary, *output.findings, *(item["title"] for item in output.recommendations)])
        for keyword in ("胸闷胸痛", "中医护理", "联系医生", "血氧"):
            self.assertIn(keyword, text)
        self.assertEqual(output.workflow_type, WorkflowType.RECOMMENDATION)
        self.assertTrue(output.context_hit)


    def test_run_recommendation_handles_no_patient_chest_pain_without_asking_bed(self) -> None:
        output = self._run_recommendation_with_contexts("不针对具体患者，胸闷胸痛时护士床旁先做什么", [])
        text = "\n".join([output.summary, *output.findings, *(item["title"] for item in output.recommendations)])
        self.assertEqual(output.workflow_type, WorkflowType.RECOMMENDATION)
        self.assertIn("胸闷胸痛", text)
        self.assertIn("联系医生", text)
        self.assertNotIn("先补充床号", output.summary)

    def test_run_recommendation_handles_ward_todo_and_handover_query(self) -> None:
        ctx1 = self._build_context(bed_no="12", patient_id="pat-001", patient_name="张晓明")
        ctx1.update({"risk_tags": ["低氧风险"], "pending_tasks": ["复测生命体征"]})
        ctx2 = self._build_context(bed_no="31", patient_id="pat-002", patient_name="邓宏")
        ctx2.update({"risk_tags": ["肝性脑病风险"], "pending_tasks": ["下一班重点观察意识变化"]})
        output = self._run_recommendation_with_contexts("按病区整理今日待办和交接重点", [ctx1, ctx2])
        text = "\n".join([output.summary, *output.findings, *(item["title"] for item in output.recommendations)])
        self.assertEqual(output.workflow_type, WorkflowType.RECOMMENDATION)
        self.assertIn("今日待办", output.summary)
        self.assertIn("交接重点", text)
        self.assertIn("12床", text)
        self.assertIn("31床", text)

    def test_finalize_enriches_closed_loop_terms_and_fallback_artifacts(self) -> None:
        payload = WorkflowRequest(
            workflow_type=WorkflowType.AUTONOMOUS_CARE,
            requested_by="u_test",
            user_input="请按晨间巡检闭环整理病区任务，补交班草稿、文书草稿、人工确认和提交前复核。",
        )
        output = WorkflowOutput(
            workflow_type=WorkflowType.AUTONOMOUS_CARE,
            summary="已完成初步分析。",
            findings=[],
            recommendations=[],
            confidence=0.82,
            review_required=True,
            created_at=datetime.now(timezone.utc),
        )
        finalized = agentic_orchestrator.finalize(payload, output)
        artifact_kinds = {item.kind for item in finalized.artifacts}
        self.assertIn("handover_batch", artifact_kinds)
        self.assertIn("document_plan", artifact_kinds)
        text = "\n".join([finalized.summary, *finalized.findings, *(item.get("title") or "" for item in finalized.recommendations)])
        for keyword in ("交班草稿", "文书草稿", "人工确认", "提交前复核"):
            self.assertIn(keyword, text)

    def test_finalize_enriches_role_specific_long_dialog_language(self) -> None:
        payload = WorkflowRequest(
            workflow_type=WorkflowType.AUTONOMOUS_CARE,
            requested_by="u_test",
            user_input="请给我护士长总览版和责任护士执行版的闭环安排，交班后还要持续追踪。",
        )
        output = WorkflowOutput(
            workflow_type=WorkflowType.AUTONOMOUS_CARE,
            summary="已完成任务拆解。",
            findings=[],
            recommendations=[],
            confidence=0.84,
            review_required=True,
            created_at=datetime.now(timezone.utc),
        )
        finalized = agentic_orchestrator.finalize(payload, output)
        text = "\n".join([finalized.summary, *finalized.findings, *(item.get("title") or "" for item in finalized.recommendations)])
        for keyword in ("护士长", "总览版", "责任护士", "执行版", "二次复核", "持续闭环"):
            self.assertIn(keyword, text)


    def test_finalize_enriches_dual_nurse_assignment_language(self) -> None:
        payload = WorkflowRequest(
            workflow_type=WorkflowType.AUTONOMOUS_CARE,
            requested_by="u_test",
            user_input="请按夜班双护士分工给我可执行的闭环安排，要写清谁去处理高风险床位。",
        )
        output = WorkflowOutput(
            workflow_type=WorkflowType.AUTONOMOUS_CARE,
            summary="已完成初步排序。",
            findings=[],
            recommendations=[],
            confidence=0.84,
            review_required=True,
            created_at=datetime.now(timezone.utc),
        )
        finalized = agentic_orchestrator.finalize(payload, output)
        text = "\n".join([finalized.summary, *finalized.findings, *(item.get("title") or "" for item in finalized.recommendations), *finalized.next_actions])
        for keyword in ("双护士", "护士A", "护士B", "高风险床位", "联系医生"):
            self.assertIn(keyword, text)

    def test_risk_score_prioritizes_hypoxia_over_pain_only(self) -> None:
        engine = AgentStateMachine()
        hypoxia_context = {
            "risk_tags": ["低氧风险"],
            "pending_tasks": ["低氧风险上报医生"],
            "latest_observations": [
                {"name": "SpO2", "value": "89%", "abnormal_flag": "high"},
                {"name": "呼吸频率", "value": "28次/分", "abnormal_flag": "high"},
            ],
        }
        pain_context = {
            "risk_tags": [],
            "pending_tasks": ["更新术后护理记录"],
            "latest_observations": [
                {"name": "疼痛评分", "value": "7/10", "abnormal_flag": "high"},
            ],
        }
        self.assertGreater(engine._risk_score(hypoxia_context), engine._risk_score(pain_context))


    def test_route_intent_prefers_document_for_template_workbench_prompt(self) -> None:
        engine = AgentStateMachine()
        prompt = (
            "请说明模板正文预览、归档床位、Word 正文、Excel 表格、结构化字段、归档预览、草稿区、提交审核、归档入病例这套工作台应该怎么用，"
            "重点说清楚模板文书先保存草稿、审核后再归档。"
        )
        routed = asyncio.run(engine.route_intent(prompt))
        self.assertEqual(routed, WorkflowType.DOCUMENT)

    def test_run_document_returns_template_workbench_guidance(self) -> None:
        output = self._run_document_with_contexts(
            "请按模板正文预览、归档床位、Word 正文、Excel 表格、结构化字段、归档预览、草稿区、提交审核、归档入病例的顺序说明临床文书工作台流程。",
            [self._build_context(bed_no="12", patient_id="pat-001", patient_name="张晓明")],
        )
        text = "\n".join([output.summary, *output.findings, *(item["title"] for item in output.recommendations)])
        for keyword in ("模板正文预览", "归档床位", "Word 正文", "Excel 表格", "结构化字段", "提交审核", "归档入病例"):
            self.assertIn(keyword, text)
        self.assertEqual(output.workflow_type, WorkflowType.DOCUMENT)
        self.assertTrue(output.context_hit)


if __name__ == "__main__":
    unittest.main()
