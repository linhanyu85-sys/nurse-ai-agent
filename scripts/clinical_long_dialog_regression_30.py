from __future__ import annotations

from clinical_long_dialog_regression_20 import build_cases as build_base_cases
from clinical_long_dialog_regression_20 import build_long_prompt
from clinical_regression_common import RegressionCase, run_suite


def extra_cases() -> list[RegressionCase]:
    return [
        RegressionCase(
            name="病区今日待办与交接摘要联动",
            category="长对话·AI Agent",
            mode="agent_cluster",
            execution_profile="full_loop",
            user_input=build_long_prompt(
                scene="白班中段整理协作模块的今日待办",
                ward_summary="护士长要求把病区今天最该盯的风险、还没完成的文书、待医生确认的事项和下一班必须承接的观察点都统一收进协作模块的今日待办，避免护士在消息、草稿、交接班之间来回翻。",
                patient_points=[
                    "12床低血压伴少尿，补液后血压略回升但仍需继续评估尿量和末梢灌注。",
                    "18床低氧反复波动，夜班已提示吸氧后仍需密切看呼吸频率和血氧。",
                    "23床拟输血，输血护理记录和交接班重点都还没完全统一。",
                    "16床高血糖合并伤口感染风险，今天已有血糖测量但交接班还缺观察重点。",
                ],
                pain_points=[
                    "临床协作最痛苦的是信息散在多个模块里，护士知道要做什么，却没有一个按优先级整理好的待办列表。",
                    "今天待办里不仅要写任务，还要说明为什么今天必须盯、谁需要复核、哪些文书需要先起草。",
                ],
                asks=[
                    "请按协作模块的今日待办形式输出，先写最优先事项，再写次优先事项。",
                    "每个待办都要说明涉及哪张床、风险点、下一步动作、是否需要医生、是否需要文书。",
                    "最后补一段适合展示在协作首页顶部的今日交接摘要。",
                ],
                constraints=[
                    "必须出现今日待办、交接摘要、人工复核、联系医生、文书草稿这些词。",
                    "不能泛泛而谈，要能直接放进软件里展示。",
                ],
            ),
            expect_workflows=("autonomous_care",),
            expected_keywords=("今日待办", "交接摘要", "人工复核", "联系医生", "文书草稿"),
            expect_artifact_kinds=("document_plan",),
            min_answer_length=260,
            min_prompt_length=650,
            require_context_hit=True,
            max_elapsed_sec=90,
        ),
        RegressionCase(
            name="体温单结构化字段补录长对话",
            category="长对话·文书",
            mode="agent_cluster",
            execution_profile="agent",
            user_input=build_long_prompt(
                scene="护士准备按电子表格方式补录体温单",
                ward_summary="老师演示时会直接看 AI 生成的体温单是不是像电子文书表格一样能拆成眉栏、一般项目、生命体征和特殊项目，而不是一大段纯文本。",
                patient_points=[
                    "16床 15:00 体温 38.4℃，18:50 复测 38.1℃，物理降温后 20:00 为 37.6℃。",
                    "患者同时有感染扩散风险，今晚还需要继续 4 小时复测并在护理记录里补异常变化说明。",
                ],
                pain_points=[
                    "体温单最容易漏的是日期、测量时间、发热复测和降温后记录逻辑。",
                    "老师通常会问系统能不能把每个字段拆出来让护士修改。",
                ],
                asks=[
                    "请按结构化体温单字段输出补录思路，至少体现眉栏、一般项目、生命体征、特殊项目。",
                    "说明哪些字段适合 AI 先填，哪些字段必须护士最后确认。",
                    "最后给一段适合归档前人工复核的提醒。",
                ],
                constraints=[
                    "必须出现眉栏、一般项目、生命体征、特殊项目、人工复核。",
                    "必须体现复测和降温后记录逻辑。",
                ],
            ),
            expect_workflows=("document_generation",),
            expected_keywords=("眉栏", "一般项目", "生命体征", "特殊项目", "人工复核"),
            min_answer_length=220,
            min_prompt_length=600,
            max_elapsed_sec=80,
        ),
        RegressionCase(
            name="输血护理记录与归档闭环",
            category="长对话·文书",
            mode="agent_cluster",
            execution_profile="agent",
            user_input=build_long_prompt(
                scene="输血前后文书、交接班和病例归档要一次讲清",
                ward_summary="老师会追问文书草稿是不是先在草稿区、护士审核后再归档到患者病例里，以及输血护理记录中的关键时间点能否被系统自动提示。",
                patient_points=[
                    "23床 A+ 型，拟输注红细胞 1 袋，医生口头交代重点观察发热、寒战、呼吸困难。",
                    "家属很紧张，护士希望系统既能先起草文书，又能提醒双人核对和归档前复核。",
                ],
                pain_points=[
                    "输血记录容易漏掉开始时间、15 分钟监测、结束后 60 分钟内复评。",
                    "提交以后如果还留在草稿区，会把界面越堆越乱。",
                ],
                asks=[
                    "先给出输血护理记录结构化草稿框架。",
                    "再说明草稿区、审核、提交归档到患者病例的闭环顺序。",
                    "最后补一段适合交给下一班的输血交接提醒。",
                ],
                constraints=[
                    "必须出现草稿区、审核、归档、双人核对、15分钟、60分钟。",
                    "输出要贴合真实输血护理流程。",
                ],
            ),
            expect_workflows=("document_generation",),
            expected_keywords=("草稿区", "审核", "归档", "双人核对", "15分钟", "60分钟"),
            min_answer_length=220,
            min_prompt_length=620,
            max_elapsed_sec=80,
        ),
        RegressionCase(
            name="病危护理记录与少尿闭环",
            category="长对话·文书",
            mode="agent_cluster",
            execution_profile="agent",
            user_input=build_long_prompt(
                scene="病危患者护理记录要写得像真实危重护理表格",
                ward_summary="用户希望老师看到的是可用的病危护理记录草稿，不只是说教式建议，要把生命体征、出入量、病情观察和护理措施写成半结构化电子文书。",
                patient_points=[
                    "12床低血压伴少尿，上午曾有意识反应差和末梢偏凉，补液后略有改善。",
                    "夜班已交代继续监测尿量、末梢灌注、血压趋势，并警惕进一步恶化。",
                ],
                pain_points=[
                    "病危护理记录最怕写成一句空话，看不出护理连续性。",
                    "老师会看是否真的体现了至少每 4 小时记录一次的逻辑和下一班观察要点。",
                ],
                asks=[
                    "请给出病危护理记录的标准化字段和正文草稿逻辑。",
                    "明确哪些内容必须人工补实测值，哪些内容适合 AI 先整理。",
                    "加一段下一班观察重点和提交前复核清单。",
                ],
                constraints=[
                    "必须出现生命体征、出入量、病情观察、护理措施、下一班观察重点。",
                    "不能泛泛而谈，要像真实表格化护理文书。",
                ],
            ),
            expect_workflows=("document_generation",),
            expected_keywords=("生命体征", "出入量", "病情观察", "护理措施", "下一班观察重点"),
            min_answer_length=220,
            min_prompt_length=620,
            max_elapsed_sec=80,
        ),
        RegressionCase(
            name="全病区白班收尾一致性复核",
            category="长对话·AI Agent",
            mode="agent_cluster",
            execution_profile="full_loop",
            user_input=build_long_prompt(
                scene="白班结束前 40 分钟做全病区一致性复核",
                ward_summary="老师很可能会问 AI Agent 是不是只会生成文书，还是能检查交接班、一般护理记录和医护沟通是否前后一致，这次要直接把一致性复核逻辑说透。",
                patient_points=[
                    "12床上午有低血压和少尿趋势，已经电话联系过医生，但一般护理记录和交接班还没完全统一。",
                    "23床输血准备中，输血前评估和交接班重点有重叠但也有遗漏。",
                    "16床血糖和伤口感染风险在护理记录里写了，但交接班还缺下一班观察点。",
                ],
                pain_points=[
                    "口头汇报、交接班和护理记录前后不一致，是临床最怕的断点之一。",
                    "老师会关注系统有没有真正的复核逻辑，而不是只会堆字。",
                ],
                asks=[
                    "请输出一份白班收尾一致性复核清单。",
                    "指出哪些床位、哪些字段、哪些医护沟通最需要补齐。",
                    "最后给一段适合展示在协作模块今日待办中的复核摘要。",
                ],
                constraints=[
                    "必须出现一致性、关键字段、人工复核、交接班、护理记录。",
                    "要能直接指导护士收尾，不是原则汇总。",
                ],
            ),
            expect_workflows=("autonomous_care",),
            expected_keywords=("一致性", "关键字段", "人工复核", "交接班", "护理记录"),
            expect_artifact_kinds=("document_plan",),
            min_answer_length=250,
            min_prompt_length=650,
            require_context_hit=True,
            max_elapsed_sec=90,
        ),
        RegressionCase(
            name="高龄躁动患者沟通与防跌倒闭环",
            category="长对话·AI Agent",
            mode="agent_cluster",
            execution_profile="full_loop",
            user_input=build_long_prompt(
                scene="高龄躁动患者夜间护理协作",
                ward_summary="20床情绪烦躁、反复下床，夜间人手紧张，老师会看系统能不能把安全、沟通、交接班和文书联动起来。",
                patient_points=[
                    "20床高龄，夜间躁动，反复想自行下床去厕所，跌倒风险高。",
                    "家属配合度一般，护士既要持续安抚又要留痕，还要把关键风险提醒给下一班。",
                ],
                pain_points=[
                    "这种患者不是只写防跌倒宣教就够，还要安排巡视、床旁环境、陪护沟通和异常升级阈值。",
                    "演示时如果 AI 只回答几句常识，会很容易被老师看出不够临床。",
                ],
                asks=[
                    "请按 AI Agent 闭环任务输出夜间处理方案。",
                    "要包括床旁动作、陪护沟通、异常升级、文书留痕和下一班交接。",
                    "最后给一句适合放进交接班报告的摘要。",
                ],
                constraints=[
                    "必须出现跌倒风险、陪护沟通、文书留痕、下一班、联系医生。",
                    "要能体现夜间实际执行顺序。",
                ],
            ),
            expect_workflows=("autonomous_care",),
            expected_keywords=("跌倒风险", "陪护沟通", "文书留痕", "下一班", "联系医生"),
            expect_artifact_kinds=("document_plan",),
            min_answer_length=240,
            min_prompt_length=620,
            require_context_hit=True,
            max_elapsed_sec=90,
        ),
        RegressionCase(
            name="血糖谱与伤口感染联动文书",
            category="长对话·文书",
            mode="agent_cluster",
            execution_profile="agent",
            user_input=build_long_prompt(
                scene="糖尿病患者血糖谱与伤口护理需要合并考虑",
                ward_summary="16床既要补血糖记录，又要把伤口感染风险和下一班观察重点写进护理文书，老师会看系统是不是能把两类信息整合，而不是各写各的。",
                patient_points=[
                    "16床今天已测餐前、餐后和随机血糖，数值有波动。",
                    "患者足部伤口渗液增多，感染风险上升，需要和血糖控制一起写进观察记录。",
                ],
                pain_points=[
                    "血糖测量单和一般护理记录往往分开写，临床真正需要的是彼此呼应。",
                    "下一班最想知道的是血糖趋势、伤口变化和异常何时上报。",
                ],
                asks=[
                    "先给出血糖测量记录单的结构化草稿重点。",
                    "再给出一般护理记录里该如何衔接伤口感染风险。",
                    "最后补一句交接班提醒。",
                ],
                constraints=[
                    "必须出现血糖测量记录单、一般护理记录、伤口感染、下一班。",
                    "输出要体现多文书联动。",
                ],
            ),
            expect_workflows=("document_generation",),
            expected_keywords=("血糖测量记录单", "一般护理记录", "伤口感染", "下一班"),
            min_answer_length=210,
            min_prompt_length=600,
            max_elapsed_sec=80,
        ),
        RegressionCase(
            name="中医护理问诊与病区协作长对话",
            category="长对话·AI Agent",
            mode="agent_cluster",
            execution_profile="full_loop",
            cluster_profile="tcm_nursing_cluster",
            user_input=build_long_prompt(
                scene="病区希望把中医护理特色真正纳入协作流程",
                ward_summary="老师会重点看中医护理是不是只是一个噱头，还是能跟普通病区任务、交接班和护理记录结合起来。",
                patient_points=[
                    "18床低氧、乏力、食欲差，家属很关心有没有中医护理观察点。",
                    "16床伤口恢复慢、睡眠差、情绪烦躁，护士希望补充中医护理观察和饮食调护提醒。",
                ],
                pain_points=[
                    "中医护理最怕只讲大而空的辨证名词，没有落到护士怎么观察、怎么交接、怎么写文书。",
                    "如果不能和普通护理任务串起来，老师会觉得这只是多加了一个模型名字。",
                ],
                asks=[
                    "请输出一个可执行的中医护理协作清单。",
                    "明确哪些证候线索适合护士观察，哪些变化需要转医生。",
                    "最后写出如何把这些内容纳入交接班和护理记录。",
                ],
                constraints=[
                    "必须出现证候、饮食、情志、交接班、护理记录。",
                    "要体现中西护理协同，不要只讲概念。",
                ],
            ),
            expect_workflows=("autonomous_care",),
            expected_keywords=("证候", "饮食", "情志", "交接班", "护理记录"),
            expect_artifact_kinds=("document_plan",),
            min_answer_length=250,
            min_prompt_length=650,
            require_context_hit=True,
            max_elapsed_sec=90,
        ),
        RegressionCase(
            name="手术物品清点记录与交接",
            category="长对话·文书",
            mode="agent_cluster",
            execution_profile="agent",
            user_input=build_long_prompt(
                scene="术后回病房前补手术物品清点记录和交接",
                ward_summary="用户要演示的是手术物品清点记录不只是一个术中表单名称，而是能输出哪些字段必须双人确认、哪些异常必须记录并上报。",
                patient_points=[
                    "术中曾追加器械与敷料，巡回护士需要即时补录。",
                    "清点结果最终一致，但中间一度存在数量不符，需要写明查找与确认经过。",
                ],
                pain_points=[
                    "这类记录最怕空泛模板，没有把双人唱点、原位清点、异常查找写清楚。",
                    "术后交接时还要把特殊物品和异常经过带给下一环节。",
                ],
                asks=[
                    "请给出手术物品清点记录的结构化草稿重点。",
                    "说明哪些字段必须双人确认，哪些异常必须写进记录。",
                    "补一句术后回病房交接提示。",
                ],
                constraints=[
                    "必须出现双人清点、即刻记录、异常查找、交接。",
                    "输出要像真实清点记录。",
                ],
            ),
            expect_workflows=("document_generation",),
            expected_keywords=("双人清点", "即刻记录", "异常查找", "交接"),
            min_answer_length=200,
            min_prompt_length=560,
            max_elapsed_sec=80,
        ),
        RegressionCase(
            name="全病区晨会讲评版 AI Agent 输出",
            category="长对话·AI Agent",
            mode="agent_cluster",
            execution_profile="full_loop",
            user_input=build_long_prompt(
                scene="护士长晨会讲评前的全病区总览",
                ward_summary="老师如果现场提问，经常会希望看到一个像护士长讲评提纲一样的输出，既要有病区风险，又要有文书、交接班、医生沟通和下一步安排。",
                patient_points=[
                    "12床低血压少尿，需继续盯血压趋势和尿量。",
                    "18床低氧、呼吸频率波动，需持续吸氧效果评估。",
                    "23床输血准备中，需完成输血护理记录与交接统一。",
                    "16床高血糖合并伤口感染风险，需把血糖和伤口记录串起来。",
                ],
                pain_points=[
                    "普通答题型 AI 往往不能把病区工作像晨会讲评一样串起来。",
                    "真正实用的 AI Agent 应该能给护士长一个可直接拿去说、拿去派工的提纲。",
                ],
                asks=[
                    "请输出护士长晨会讲评提纲。",
                    "提纲要包含病区风险排序、文书状态、医生沟通重点、今日待办和下一班承接点。",
                    "最后给一句总结，说明今天最不能出错的风险在哪里。",
                ],
                constraints=[
                    "必须出现风险排序、文书状态、医生沟通、今日待办、下一班。",
                    "输出要像晨会讲评，不是普通问答。",
                ],
            ),
            expect_workflows=("autonomous_care",),
            expected_keywords=("风险排序", "文书状态", "医生沟通", "今日待办", "下一班"),
            expect_artifact_kinds=("document_plan",),
            min_answer_length=260,
            min_prompt_length=650,
            require_context_hit=True,
            max_elapsed_sec=90,
        ),
    ]


def build_cases() -> list[RegressionCase]:
    cases = list(build_base_cases()) + extra_cases()
    assert len(cases) == 30, len(cases)
    return cases


if __name__ == "__main__":
    raise SystemExit(
        run_suite(
            suite_name="30组千字级临床AI Agent长对话回归",
            suite_id="clinical_long30",
            report_filename="clinical_long_dialog_regression_30.json",
            cases=build_cases(),
        )
    )
