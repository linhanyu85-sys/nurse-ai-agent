from __future__ import annotations

import sys

sys.path.insert(0, r"D:\Projects\ai_agent_local\scripts")
sys.path.insert(0, r"D:\Projects\ai_agent_local\services\agent-orchestrator")

import clinical_long_dialog_regression_20 as suite
from app.services.state_machine import AgentStateMachine, machine


async def main() -> None:
    checks = {
        5: ("输血护理记录", "草稿", "交班", "双人核对"),
        19: ("引流", "鲜红", "联系医生", "交班"),
    }
    for idx, tokens in checks.items():
        question = suite.build_cases()[idx - 1].user_input
        print("=" * 80)
        print(idx, suite.build_cases()[idx - 1].name)
        print("infer_document_type:", AgentStateMachine._infer_document_type(question))
        print("route_intent:", await machine.route_intent(question))
        bedside_management_tokens = (
            "床旁先后顺序",
            "先看什么",
            "先做什么",
            "先处理什么",
            "先复核什么",
            "观察重点",
            "床旁观察重点",
            "重新评估",
            "再评估",
            "何时联系医生",
            "什么时候联系医生",
            "何时找医生",
            "什么时候找医生",
            "先床旁复核",
        )
        explicit_document_tokens = (
            "体温单",
            "输血护理记录",
            "输血",
            "血液输注",
            "血糖测量记录",
            "血糖谱",
            "POCT",
            "一般护理记录",
            "护理记录单",
            "病重护理记录",
            "病危护理记录",
            "病重",
            "病危",
            "危重护理",
            "手术物品清点",
            "清点记录",
        )
        document_action_tokens = (
            "草稿",
            "补录",
            "录入",
            "生成",
            "起草",
            "电子录入",
            "记录单",
            "记录思路",
            "字段",
            "人工补写",
            "AI起草",
            "AI 起草",
            "先起草",
            "先生成",
        )
        print(
            "doc_rule:",
            any(token in question for token in explicit_document_tokens)
            and any(token in question for token in document_action_tokens),
        )
        print(
            "device_recommend_rule:",
            any(token in question for token in ("导尿管", "留置导尿", "尿液混浊", "下腹不适", "引流", "鲜红", "切口情况", "补液平衡", "液体丢失"))
            and (
                any(token in question for token in bedside_management_tokens)
                or any(token in question for token in ("联系医生", "护理记录", "交班", "下一班"))
            ),
        )
        condition_trace = [
            ("handover_order", any(token in question for token in ("护理日夜交接班报告按什么顺序", "交接班报告按什么顺序", "交班报告按什么顺序"))),
            ("handover_fixed_draft", any(token in question for token in ("白班护理交接班草稿", "全病区交接班草稿", "生成今天这个病区的白班护理交接班草稿"))),
            (
                "handover_compare",
                bool(machine._extract_beds(question)) and any(token in question for token in ("交班提醒", "一句话交班", "一句话提醒", "比较交班", "对比交班")),
            ),
            ("recommend_top_five", any(token in question for token in ("前五个高危重点", "前五个高危", "交代给下一班的前五个高危"))),
            ("recommend_triage", any(token in question for token in ("谁能等", "谁不能等", "马上处理", "30分钟内处理", "可以稍后处理", "分类依据"))),
            ("doc_rule", any(token in question for token in explicit_document_tokens) and any(token in question for token in document_action_tokens)),
            (
                "device_recommend_rule",
                any(token in question for token in ("导尿管", "留置导尿", "尿液混浊", "下腹不适", "引流", "鲜红", "切口情况", "补液平衡", "液体丢失"))
                and (any(token in question for token in bedside_management_tokens) or any(token in question for token in ("联系医生", "护理记录", "交班", "下一班"))),
            ),
            ("tcm_recommend", machine._is_tcm_question(question) and any(token in question for token in ("证候", "饮食", "情志", "护理观察", "转医生", "联系医生"))),
            ("tcm_voice", machine._is_tcm_question(question) and not machine._extract_beds(question)),
            (
                "bedside_management_rule",
                any(token in question for token in bedside_management_tokens)
                and not any(token in question for token in ("交班草稿", "交接班草稿", "交班报告", "交接班报告", "白班护理交接班草稿", "全病区交接班草稿")),
            ),
            ("document_guidance", machine._is_document_guidance_query(question)),
            ("handover_guidance", machine._is_handover_guidance_query(question)),
            ("autonomous", False),
            ("doctor_escalation", machine._is_doctor_escalation_request(question)),
            ("monitor_schedule", machine._is_monitoring_schedule_request(question)),
            ("handover_fallback", any(t in question.lower() for t in machine.HANDOVER_TOKENS) or any(t in question for t in ("交班", "交接班"))),
        ]
        for name, value in condition_trace:
            if value:
                print("first_true_candidate:", name)
                break
        for token in tokens:
            print(f"{token} => {token in question}")


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
