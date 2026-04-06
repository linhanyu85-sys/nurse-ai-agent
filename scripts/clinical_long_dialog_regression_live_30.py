from __future__ import annotations

from clinical_regression_common import RegressionCase, run_suite


def build_long_prompt(
    *,
    scene: str,
    ward_summary: str,
    patient_points: list[str],
    pain_points: list[str],
    asks: list[str],
    constraints: list[str],
) -> str:
    return (
        f"你现在处在{scene}。{ward_summary}\n"
        "请把下面内容当成护士在真实临床班次里一次性交给 AI Agent 的长任务，不要把它当成普通聊天，也不要只给几句原则性建议。"
        "我希望你像一个真正能服务临床的护理协作系统一样，把病区风险、护理观察、医生沟通、文书草稿、交接班和待办闭环串起来。\n"
        "当前已经掌握的患者与病区情况如下：\n"
        + "\n".join(f"{idx + 1}. {item}" for idx, item in enumerate(patient_points))
        + "\n当前最容易出错、最需要系统帮忙兜住的临床痛点有：\n"
        + "\n".join(f"- {item}" for item in pain_points)
        + "\n这次我希望你按可执行工作流完成下面这些事：\n"
        + "\n".join(f"{idx + 1}. {item}" for idx, item in enumerate(asks))
        + "\n回答时必须严格满足这些要求：\n"
        + "\n".join(f"- {item}" for item in constraints)
        + "\n如果你要输出交接班、护理记录、待办清单或文书草稿，请尽量使用临床护理常用表达，体现优先级、时间点、观察重点、异常升级阈值、人工复核内容、下一班承接事项和归档前复核逻辑。"
        + "\n请不要只给概念性建议，而要像真正的临床护理 AI Agent 一样，把病区风险热力图、今日待办时间轴、交接班摘要看板、文书草稿区、人工审核和自动归档之间的关系说清楚。"
        + "\n如果涉及一般临床问题，请像带教老师一样直接作答，不要强行要求补床号；如果涉及患者或病区任务，请明确先做什么、什么时候复评、何时联系医生、哪些字段要补、哪些内容留给下一班。"
    )


def make_case(
    *,
    name: str,
    category: str,
    scene: str,
    ward_summary: str,
    patient_points: list[str],
    pain_points: list[str],
    asks: list[str],
    constraints: list[str],
    mode: str = "agent_cluster",
    execution_profile: str | None = "agent",
    cluster_profile: str | None = "nursing_default_cluster",
    expect_workflows: tuple[str, ...] = (),
    expected_keywords: tuple[str, ...] = (),
    expect_artifact_kinds: tuple[str, ...] = (),
    require_context_hit: bool | None = None,
    min_answer_length: int = 220,
    min_prompt_length: int = 620,
    max_elapsed_sec: float = 90,
) -> RegressionCase:
    return RegressionCase(
        name=name,
        category=category,
        mode=mode,
        execution_profile=execution_profile,
        cluster_profile=cluster_profile,
        user_input=build_long_prompt(
            scene=scene,
            ward_summary=ward_summary,
            patient_points=patient_points,
            pain_points=pain_points,
            asks=asks,
            constraints=constraints,
        ),
        expect_workflows=expect_workflows,
        expected_keywords=expected_keywords,
        expect_artifact_kinds=expect_artifact_kinds,
        require_context_hit=require_context_hit,
        min_answer_length=min_answer_length,
        min_prompt_length=min_prompt_length,
        max_elapsed_sec=max_elapsed_sec,
    )


def build_cases() -> list[RegressionCase]:
    cases: list[RegressionCase] = []
    cases.extend(
        [
            make_case(
                name="病区今日待办与交接摘要联动",
                category="长对话·AI Agent",
                scene="心内科白班晨间梳理病区任务",
                ward_summary="护士长要求把整病区最该先盯的风险、待办、医生沟通和文书闭环集中到协作模块的今日待办里，避免护士在消息、草稿和交接班之间来回翻。",
                patient_points=[
                    "12床收缩压持续在 88 至 92 mmHg 之间波动，尿量减少，昨夜已补液但改善有限，需要继续看末梢灌注和尿量。",
                    "18床夜间最低血氧到 89%，吸氧后回到 92% 左右，呼吸频率偏快，护士担心再翻身后又掉下去。",
                    "23床感染合并贫血，今天拟输血，输血护理记录、一般护理记录和交接班重点还没有完全统一。",
                    "16床高血糖合并足部伤口感染风险，血糖测量已经做了几次，但下一班观察点没有写清。",
                ],
                pain_points=[
                    "病区最怕信息散在不同模块里，知道谁危险，却不知道先处理什么、先补哪张文书、先联系谁。",
                    "老师会盯着看系统能不能把今日待办、交接摘要、人工复核和文书草稿真正串起来，而不是一堆分散建议。",
                ],
                asks=[
                    "按今日待办的形式输出优先级顺序，并说明立即处理、30 分钟内处理和可交下一班承接的事项。",
                    "每一项都写清楚涉及床位、风险点、下一步动作、是否需要联系医生、是否需要先起草文书。",
                    "最后给一段适合直接展示在协作模块顶部的交接摘要。",
                ],
                constraints=[
                    "必须出现今日待办、交接摘要、人工复核、联系医生、文书草稿这些词。",
                    "不要要求我再补床号，因为这是病区级任务。",
                ],
                execution_profile="full_loop",
                expect_workflows=("autonomous_care",),
                expected_keywords=("今日待办", "交接摘要", "人工复核", "联系医生", "文书草稿"),
                expect_artifact_kinds=("document_plan",),
                require_context_hit=True,
                min_answer_length=260,
            ),
            make_case(
                name="晨间巡检风险排序与病区热力图口径",
                category="长对话·AI Agent",
                scene="普外科白班晨间巡检开始前",
                ward_summary="老师会看系统给出的风险排序是否真正符合临床，是否能转成病区风险热力图和护士巡视频次建议。",
                patient_points=[
                    "12床低血压少尿趋势仍在，补液后血压略升但尿量恢复不理想。",
                    "18床低氧边缘波动，咳痰无力，吸氧后仍需要反复复评。",
                    "20床高龄躁动、反复下床，跌倒风险高，家属陪护不稳定。",
                    "16床血糖波动伴伤口感染风险，渗液较昨晚增多。",
                ],
                pain_points=[
                    "临床不缺风险标签，缺的是按床位动态排序和可执行巡查顺序。",
                    "热力图不能只显示颜色，必须让护士一眼知道先看谁、盯什么。",
                ],
                asks=[
                    "给出病区风险热力图口径，明确危急、高危、中危、低危四层分级。",
                    "按床位排出晨间优先巡查顺序，并说明每床第一观察重点。",
                    "补一段适合放在热力图下面的护士行动提示。",
                ],
                constraints=[
                    "必须出现危急、高危、中危、低危、优先巡查、观察重点。",
                    "回答要能直接放进首页可视化里。",
                ],
                execution_profile="full_loop",
                expect_workflows=("autonomous_care",),
                expected_keywords=("危急", "高危", "中危", "低危", "优先巡查", "观察重点"),
                expect_artifact_kinds=("document_plan",),
                require_context_hit=True,
                min_answer_length=250,
            ),
            make_case(
                name="夜班双护士分工闭环",
                category="长对话·AI Agent",
                scene="外科夜班仅两名护士值班",
                ward_summary="夜班最怕优先级排错，老师会看 AI Agent 能不能真的按两名护士分工，而不是只给泛泛建议。",
                patient_points=[
                    "17床术后疼痛评分 7 分，刚给镇痛药，待到峰值时间复评。",
                    "18床低氧风险高，翻身后容易掉饱和度，需要看吸氧效果和呼吸频率。",
                    "12床低血压少尿趋势在延续，需要继续看尿量、末梢灌注和血压回升情况。",
                    "20床高龄躁动，夜间反复想下床，跌倒风险高。",
                ],
                pain_points=[
                    "夜班不是不知道风险，而是很难把立即处理、30 分钟内处理和留给下一班承接区分清楚。",
                    "真正好用的系统要像护士长一样帮忙分工，而不是只讲原则。",
                ],
                asks=[
                    "按两名护士的现实条件做出夜班分工表。",
                    "把事项分成马上处理、30 分钟内处理、下一班承接三层。",
                    "写清楚每床何时联系医生、何时先床旁复评。",
                ],
                constraints=[
                    "必须出现马上处理、30分钟内处理、下一班、联系医生。",
                    "输出必须能被夜班护士直接照着执行。",
                ],
                execution_profile="full_loop",
                expect_workflows=("autonomous_care",),
                expected_keywords=("马上处理", "30分钟内处理", "下一班", "联系医生"),
                expect_artifact_kinds=("document_plan",),
                require_context_hit=True,
                min_answer_length=250,
            ),
            make_case(
                name="白班收尾一致性复核",
                category="长对话·AI Agent",
                scene="白班结束前 40 分钟",
                ward_summary="老师会追问系统如何保证交接班、护理记录和医生沟通前后一致，不能只是会生成文书。",
                patient_points=[
                    "12床上午曾两次低血压，已经电话联系过医生，但一般护理记录和交接班还不完全一致。",
                    "23床拟输血，输血前评估、交接班重点和贫血感染的风险说明还未统一。",
                    "16床血糖和伤口感染风险都写了，但下一班观察重点不完整。",
                ],
                pain_points=[
                    "口头通知过医生但文书没补齐，是最常见也最危险的断点之一。",
                    "交班最怕只说风险，不说已经做了什么和下一班还需看什么。",
                ],
                asks=[
                    "输出一份白班收尾一致性复核清单。",
                    "指出哪些床位、哪些字段、哪些医生沟通需要补齐。",
                    "给出提交前人工复核步骤。",
                ],
                constraints=[
                    "必须出现一致性、关键字段、人工复核、交接班、护理记录。",
                    "不要只说原则，要写出真正核对的项目。",
                ],
                execution_profile="full_loop",
                expect_workflows=("autonomous_care",),
                expected_keywords=("一致性", "关键字段", "人工复核", "交接班", "护理记录"),
                expect_artifact_kinds=("document_plan",),
                require_context_hit=True,
                min_answer_length=250,
            ),
            make_case(
                name="值班医生集中汇报顺序",
                category="长对话·AI Agent",
                scene="准备电话集中汇报值班医生",
                ward_summary="临床最需要的是一个可直接照着说的汇报顺序和一床一句话的话术，而不是大段解释。",
                patient_points=[
                    "12床低血压少尿，补液后改善有限。",
                    "18床低氧边缘波动，翻身后容易掉血氧。",
                    "23床感染合并贫血，今天拟输血。",
                    "20床高龄躁动，跌倒风险高，陪护不稳定。",
                ],
                pain_points=[
                    "多床并发时容易想到什么说什么，导致真正危险的床位被延误。",
                    "电话汇报、交接班和护理记录如果口径不一致，老师会马上指出来。",
                ],
                asks=[
                    "按风险排序给出值班医生电话汇报顺序。",
                    "每床给一句电话话术，说明核心异常和希望医生决策的点。",
                    "补一段交接班和护理记录如何同步更新的提醒。",
                ],
                constraints=[
                    "必须出现排序、电话、联系医生、交接班。",
                    "输出要适合护士现场直接照着说。",
                ],
                execution_profile="full_loop",
                expect_workflows=("autonomous_care",),
                expected_keywords=("排序", "电话", "联系医生", "交接班"),
                expect_artifact_kinds=("document_plan",),
                require_context_hit=True,
            ),
            make_case(
                name="高龄躁动防跌闭环",
                category="长对话·AI Agent",
                scene="老年躁动患者夜间护理",
                ward_summary="老师会看系统能否把安全、沟通、文书、交接班和升级阈值串成闭环。",
                patient_points=[
                    "20床高龄，夜间躁动反复下床，上厕所意愿强，跌倒风险高。",
                    "陪护配合一般，护士需要持续安抚并留痕，同时要给下一班明确风险提示。",
                ],
                pain_points=[
                    "这类患者不能只给一句防跌倒宣教，必须落到床旁动作、陪护沟通、异常升级和文书留痕。",
                    "如果 AI 只回答几句常识，老师会直接觉得不够临床。",
                ],
                asks=[
                    "按 AI Agent 闭环任务输出夜间处理方案。",
                    "包括床旁动作、陪护沟通、异常升级、文书留痕和下一班交接。",
                    "最后给一句适合放进交接班报告的摘要。",
                ],
                constraints=[
                    "必须出现跌倒风险、陪护沟通、文书留痕、下一班、联系医生。",
                    "要体现夜间真实执行顺序。",
                ],
                execution_profile="full_loop",
                expect_workflows=("autonomous_care",),
                expected_keywords=("跌倒风险", "陪护沟通", "文书留痕", "下一班", "联系医生"),
                expect_artifact_kinds=("document_plan",),
                require_context_hit=True,
            ),
            make_case(
                name="术后返回病房观察闭环",
                category="长对话·AI Agent",
                scene="术后患者返回病房后的前两小时",
                ward_summary="临床希望系统能把术后回室观察、异常升级、文书起草和交接提醒一起串起来。",
                patient_points=[
                    "17床腹部手术后刚回病房，切口敷料干燥，疼痛评分偏高，恶心轻度，血压仍偏低。",
                    "家属多次询问何时能喝水、何时能翻身，护士还要同时完成术后观察与文书记录。",
                ],
                pain_points=[
                    "术后最怕只说常规观察，不说异常升级阈值和文书起草顺序。",
                    "护士需要能直接执行的前 2 小时观察清单和交班提醒。",
                ],
                asks=[
                    "按前 30 分钟、30 至 120 分钟两个阶段整理观察重点。",
                    "说明哪些异常达到什么程度要联系医生。",
                    "同步给出术后护理记录草稿和下一班交接要点。",
                ],
                constraints=[
                    "必须出现前30分钟、联系医生、护理记录草稿、下一班交接。",
                    "不能只写原则，要像真实术后观察方案。",
                ],
                execution_profile="full_loop",
                expect_workflows=("autonomous_care",),
                expected_keywords=("前30分钟", "联系医生", "护理记录草稿", "下一班交接"),
                expect_artifact_kinds=("document_plan",),
                require_context_hit=True,
            ),
            make_case(
                name="输血前后闭环协作",
                category="长对话·AI Agent",
                scene="病区准备给 23 床输血",
                ward_summary="老师会盯着看系统能不能把输血前评估、床旁双人核对、15 分钟复评、结束后观察、文书草稿和交接班一次串起来。",
                patient_points=[
                    "23床 A+ 型，拟输红细胞 1 袋，医生要求严密观察发热、寒战、呼吸困难。",
                    "家属紧张，护士既要解释，又要按规范留痕，还要提醒下一班继续盯输血反应。",
                ],
                pain_points=[
                    "输血最容易漏开始时间、15 分钟观察和结束后复评。",
                    "草稿留在草稿区、不及时归档，会让页面越来越乱。",
                ],
                asks=[
                    "按输血前、开始后15分钟、输血结束后整理任务。",
                    "给出输血护理记录和交接班的一体化闭环方案。",
                    "明确哪些内容必须双人核对、哪些必须人工复核后归档。",
                ],
                constraints=[
                    "必须出现双人核对、15分钟、60分钟、人工复核、归档。",
                    "输出必须贴合真实输血护理流程。",
                ],
                execution_profile="full_loop",
                expect_workflows=("autonomous_care", "document_generation"),
                expected_keywords=("双人核对", "15分钟", "60分钟", "人工复核", "归档"),
                expect_artifact_kinds=("document_plan",),
                require_context_hit=True,
            ),
            make_case(
                name="感染风险病区巡查闭环",
                category="长对话·AI Agent",
                scene="病区午间感染风险巡查",
                ward_summary="老师会看系统是否能把伤口、导管、发热和隔离观察整合成巡查顺序，而不是各说各的。",
                patient_points=[
                    "16床足部伤口渗液增多，血糖波动，感染风险上升。",
                    "23床发热合并拟输血，感染和输血风险需要一起盯。",
                    "12床留置导尿，尿量减少，既要看循环，也要看导管通畅与感染信号。",
                ],
                pain_points=[
                    "感染巡查不是只看体温，还要把导管、伤口、血糖、出入量和隔离要求串起来。",
                    "下一班最怕接到一句‘注意感染’，却不知道重点在哪。",
                ],
                asks=[
                    "排出感染风险巡查顺序和每床观察重点。",
                    "说明哪些异常达到什么标准要立刻升级。",
                    "给一段适合交接班的感染风险摘要。",
                ],
                constraints=[
                    "必须出现巡查顺序、观察重点、升级、交接班。",
                    "要贴合护理临床，不要泛泛而谈。",
                ],
                execution_profile="full_loop",
                expect_workflows=("autonomous_care",),
                expected_keywords=("巡查顺序", "观察重点", "升级", "交接班"),
                expect_artifact_kinds=("document_plan",),
                require_context_hit=True,
            ),
            make_case(
                name="中医护理协同闭环",
                category="长对话·AI Agent",
                scene="病区希望把中医护理真正接入日常闭环",
                ward_summary="老师会看中医护理是不是只多了个模型名字，还是能和观察、交接班、护理记录结合起来。",
                patient_points=[
                    "18床低氧、乏力、食欲差，家属关心是否能从中医护理角度补充观察和饮食建议。",
                    "16床伤口恢复慢、睡眠差、情绪烦躁，希望补充中医护理观察点和交班提示。",
                ],
                pain_points=[
                    "中医护理最怕只讲空泛证候，不落到护士怎么观察、怎么交接、怎么留痕。",
                    "如果和西医护理任务串不起来，老师会觉得只是换了个模型皮肤。",
                ],
                asks=[
                    "给出一个可执行的中医护理协作清单。",
                    "说明哪些证候线索适合护士观察，哪些变化需要及时转医生。",
                    "最后写明怎样纳入交接班和护理记录。",
                ],
                constraints=[
                    "必须出现证候、饮食、情志、交接班、护理记录。",
                    "要体现中西护理协同，不要只讲概念。",
                ],
                execution_profile="full_loop",
                cluster_profile="tcm_nursing_cluster",
                expect_workflows=("autonomous_care",),
                expected_keywords=("证候", "饮食", "情志", "交接班", "护理记录"),
                expect_artifact_kinds=("document_plan",),
                require_context_hit=True,
                min_answer_length=250,
            ),
        ]
    )
    cases.extend(
        [
            make_case(
                name="体温单补录与复测闭环",
                category="长对话·文书闭环",
                scene="责任护士准备补录 12 床体温单并核对异常波动",
                ward_summary="老师会重点看 AI 能不能把体温单标准、异常复测、降温处理、疼痛和特殊项目一起串起来，而不是只给几句原则。",
                patient_points=[
                    "12床今日 7:00 体温 36.7℃，11:00 突升到 38.4℃，15:00 降温后 37.6℃，收缩压仍偏低，尿量减少。",
                    "患者诉伤口疼痛 6 分，刚做过镇痛处理，护士还要补疼痛复评和 24 小时出入量。",
                ],
                pain_points=[
                    "体温单最容易漏掉异常复测标记、降温后红圈标识和特殊项目栏补录。",
                    "如果只生成一段文本，护士后续还得自己拆字段，实际并不好用。",
                ],
                asks=[
                    "按体温单电子草稿的思路，列出眉栏、一般项目、生命体征栏和特殊项目栏应该补什么。",
                    "说明哪些字段应标记为待补，哪些异常需要人工复核后再提交。",
                    "最后补一句适合放进交接班里的摘要。",
                ],
                constraints=[
                    "必须出现体温单、复测、降温后、疼痛复评、24小时出入量、人工复核、归档。",
                    "回答要有半结构化字段感，便于直接做成表单。",
                ],
                expected_keywords=("体温单", "复测", "降温后", "人工复核", "归档"),
                require_context_hit=True,
                min_answer_length=240,
            ),
            make_case(
                name="病重护理记录四小时闭环",
                category="长对话·文书闭环",
                scene="病重患者护理记录续写前",
                ward_summary="系统要能把生命体征频次、出入量、病情观察、护理措施和效果记录成真正能审核的草稿。",
                patient_points=[
                    "12床病重，低血压少尿趋势持续，氧饱和度波动，需继续观察末梢灌注和尿量。",
                    "夜班已记录一次补液后血压略回升，但出入量小结和下一班观察重点还未补齐。",
                ],
                pain_points=[
                    "病重记录最怕只写病情变化，不写护理措施和效果。",
                    "如果没有按至少 4 小时记录 1 次的节奏整理，老师会直接指出不符合规范。",
                ],
                asks=[
                    "按病重护理记录标准列出本班需要记录的核心栏位。",
                    "说明哪些内容要写到分钟，哪些要作为每班小结保留。",
                    "生成一段可直接进入草稿区的病重护理记录结构。",
                ],
                constraints=[
                    "必须出现至少每4小时、出入量、护理措施、效果、下一班观察重点。",
                    "不要只讲原则，要有草稿字段和记录口径。",
                ],
                expected_keywords=("至少每4小时", "出入量", "护理措施", "效果", "下一班观察重点"),
                require_context_hit=True,
                min_answer_length=240,
            ),
            make_case(
                name="输血护理记录规范化补齐",
                category="长对话·文书闭环",
                scene="23 床输血护理记录准备提交前",
                ward_summary="老师会看系统能否把输血前评估、双人核对、15 分钟复评、结束后观察和归档节点一起整理。",
                patient_points=[
                    "23床 A+ 型，计划输红细胞 1 袋，既往无明确输血反应史。",
                    "开始前生命体征已测一次，但 15 分钟复评和结束后 60 分钟内观察还未统一写进草稿。",
                ],
                pain_points=[
                    "输血记录最容易漏时间点，也容易把双人核对和人工复核混在一起。",
                    "如果草稿不能区分待补字段和可提交字段，护士审核效率会很差。",
                ],
                asks=[
                    "按输血前、开始后 15 分钟、结束后 60 分钟内三个阶段整理记录项。",
                    "列出必须双人核对、必须人工复核和可自动归档的内容。",
                    "补一段适合交接班的输血风险摘要。",
                ],
                constraints=[
                    "必须出现双人核对、15分钟、60分钟内、输血反应、人工复核、归档。",
                    "要像真实输血护理流程，不要泛泛而谈。",
                ],
                expected_keywords=("双人核对", "15分钟", "60分钟内", "输血反应", "归档"),
                require_context_hit=True,
                min_answer_length=240,
            ),
            make_case(
                name="血糖谱与伤口观察联动",
                category="长对话·文书闭环",
                scene="糖尿病患者白班收尾前整理血糖与伤口文书",
                ward_summary="系统需要把血糖谱、随机血糖补测、伤口观察和交接班重点整成一个可执行闭环。",
                patient_points=[
                    "16床早餐前、午餐前血糖偏高，晚餐前尚未测，伤口渗液增多，护士担心感染和血糖控制互相影响。",
                    "本班已经做过一次随机血糖复测，但记录单和一般护理记录没有完全同步。",
                ],
                pain_points=[
                    "血糖记录和护理观察常常分散，交班时很难说清真正风险。",
                    "老师会看系统能否把 POCT 记录、复测原因和伤口观察合并。",
                ],
                asks=[
                    "按血糖谱记录单思路列出今日还应补录的时点与字段。",
                    "说明怎样把血糖异常与伤口感染风险写进护理记录和交接班。",
                    "给出一份适合草稿区编辑的半结构化模板。",
                ],
                constraints=[
                    "必须出现餐前、随机血糖、复测、POCT、伤口、交接班。",
                    "输出要方便护士继续逐格修改。",
                ],
                expected_keywords=("餐前", "随机血糖", "复测", "POCT", "交接班"),
                require_context_hit=True,
                min_answer_length=230,
            ),
            make_case(
                name="手术物品清点记录口径统一",
                category="长对话·文书闭环",
                scene="术毕后护士准备补齐手术物品清点记录",
                ward_summary="老师会看系统是否真正理解双人逐项清点、同步唱点、关闭体腔前后等关键时机。",
                patient_points=[
                    "17床腹部手术已结束，术中追加过敷料，术毕准备完成手术物品清点记录。",
                    "巡回护士担心交接时漏记追加敷料和关闭体腔前后清点时点。",
                ],
                pain_points=[
                    "手术清点记录不是普通护理记录，关键是时机、数量、完整性和签名责任。",
                    "如果 AI 说不清关闭体腔前后和缝合皮肤后的差异，老师一眼就会发现问题。",
                ],
                asks=[
                    "按手术开始前、关闭体腔前、关闭体腔后、缝合皮肤后四个节点整理清点要求。",
                    "强调术中追加物品和必须交接时应该怎么记录。",
                    "给出一份可进入草稿编辑器的清点记录框架。",
                ],
                constraints=[
                    "必须出现双人逐项清点、同步唱点、关闭体腔前、关闭体腔后、缝合皮肤后、签名。",
                    "回答要贴近手术清点记录，不要泛化成普通护理建议。",
                ],
                expected_keywords=("双人逐项清点", "关闭体腔前", "关闭体腔后", "缝合皮肤后", "签名"),
                require_context_hit=True,
                min_answer_length=240,
            ),
            make_case(
                name="交接班报告顺序与今日重点同步",
                category="长对话·文书闭环",
                scene="白班准备生成今日护理交接班报告",
                ward_summary="老师会看系统是否能严格按交接班书写顺序，把出科、入科、病重、术后、高危患者和异常事件串起来。",
                patient_points=[
                    "上午有 1 名新入院、1 名术后返回、12床持续低血压、18床低氧边缘波动、20床高龄躁动防跌。",
                    "协作模块已经有今日待办，但需要同步生成可交班的摘要和顺序。",
                ],
                pain_points=[
                    "很多系统会把交接班写成普通总结，而不是按临床实际顺序组织。",
                    "如果交接班与今日待办口径不一致，老师会觉得系统不落地。",
                ],
                asks=[
                    "按交接班规范顺序输出今日报告框架。",
                    "把协作模块里的今日待办转成交接班重点。",
                    "给出一段可直接进入草稿区的交接班报告摘要。",
                ],
                constraints=[
                    "必须出现出科、入科、病重病危、当日手术、次日手术、高危患者、外出请假、异常事件。",
                    "输出要像真实交班，而不是普通摘要。",
                ],
                expected_keywords=("出科", "入科", "病重", "当日手术", "高危患者", "异常事件"),
                require_context_hit=True,
                min_answer_length=250,
            ),
            make_case(
                name="文书草稿审核后自动归档流程",
                category="长对话·文书闭环",
                scene="护士长审看文书流转设计是否贴合临床",
                ward_summary="老师会重点看草稿区、审核、提交、归档到患者档案这条链路是否真正闭环，并且页面是否整洁。",
                patient_points=[
                    "12床已有体温单草稿、病重护理记录草稿。",
                    "23床已有输血护理记录草稿，16床已有血糖记录草稿。",
                ],
                pain_points=[
                    "草稿、已提交和已归档如果混在一起，协作页会很乱。",
                    "护士希望能先在草稿区编辑、审核后再自动归入患者档案。",
                ],
                asks=[
                    "请按草稿区、待审核、待提交、已归档四段说明文书流转。",
                    "说明护士、审核者和系统分别做什么动作。",
                    "给出适合页面显示的短提示语和状态解释。",
                ],
                constraints=[
                    "必须出现草稿区、待审核、待提交、已归档、患者档案、人工复核。",
                    "要有页面级动作描述，不只是后端流程。",
                ],
                expected_keywords=("草稿区", "待审核", "待提交", "已归档", "患者档案", "人工复核"),
                min_answer_length=220,
            ),
            make_case(
                name="文书模板导入与字段缺失校验",
                category="长对话·文书闭环",
                scene="护理部准备导入标准模板并让 AI 辅助填报",
                ward_summary="系统要证明自己不是随便生成文本，而是能基于标准模板给出待补字段、自动回填和提交前校验。",
                patient_points=[
                    "护理部已整理体温单、病重护理记录、输血记录、血糖记录和交接班模板。",
                    "比赛展示时老师会追问模板导入后如何识别缺失字段、如何避免直接错误归档。",
                ],
                pain_points=[
                    "很多 AI 工具会直接吐一段答案，但护士真正需要的是结构化模板加可编辑字段。",
                    "如果没有提交前校验，老师会认为临床风险太大。",
                ],
                asks=[
                    "说明模板导入后 AI 如何识别字段、自动回填和标记待补。",
                    "给出提交前校验清单，包括必填项、时间点和人工复核。",
                    "补一句适合放在编辑器顶部的引导文案。",
                ],
                constraints=[
                    "必须出现模板导入、待补字段、自动回填、提交前校验、人工复核。",
                    "回答要贴近你现在这个系统的草稿编辑器设计。",
                ],
                expected_keywords=("模板导入", "待补字段", "自动回填", "提交前校验", "人工复核"),
                min_answer_length=220,
            ),
            make_case(
                name="多文书联动生成与归档顺序",
                category="长对话·文书闭环",
                scene="责任护士希望一次处理 12 床多份文书",
                ward_summary="系统要能理解同一患者的体温单、病重护理记录和交接班重点不是孤立的，应该按优先级生成和归档。",
                patient_points=[
                    "12床持续低血压少尿，需要更新体温单特殊项目、病重护理记录和交接班重点。",
                    "护士想一句话下达任务，不想一份文书一份文书地点。",
                ],
                pain_points=[
                    "多文书一起做时最容易重复写、漏同步、漏归档。",
                    "老师会看 AI Agent 能不能自动排序，而不是让人自己分派。",
                ],
                asks=[
                    "按临床优先级排序 12 床今天要补的三类文书。",
                    "说明每份文书与哪条风险或观察点直接对应。",
                    "给出生成后如何审核归档到患者档案的闭环。",
                ],
                constraints=[
                    "必须出现体温单、病重护理记录、交接班、优先级、审核、归档。",
                    "要体现同一患者多文书联动。",
                ],
                expected_keywords=("体温单", "病重护理记录", "交接班", "优先级", "归档"),
                require_context_hit=True,
                min_answer_length=230,
            ),
            make_case(
                name="患者档案树与文书检索口径",
                category="长对话·文书闭环",
                scene="护士从患者档案直接查文书而不是翻历史",
                ward_summary="老师会看系统能否把文书按患者归档、按内容搜索，并且从患者档案直接看到草稿与已归档状态。",
                patient_points=[
                    "12床、16床、23床都有不同类型的文书草稿和历史归档。",
                    "护士不想在历史里一条条找，只想点患者进去看所有文书。",
                ],
                pain_points=[
                    "如果搜索和归档路径设计不好，页面会显得很乱。",
                    "老师会问：为什么不直接从患者档案看，而要去历史列表里翻。",
                ],
                asks=[
                    "请描述患者档案页应该怎样展示草稿、待归档和已归档文书。",
                    "说明搜索栏应支持哪些关键词命中。",
                    "给出一段适合协作模块里提示护士的文案。",
                ],
                constraints=[
                    "必须出现患者档案、草稿、已归档、搜索、文书类型、状态。",
                    "要体现页面整洁和临床效率。",
                ],
                expected_keywords=("患者档案", "草稿", "已归档", "搜索", "状态"),
                min_answer_length=220,
            ),
        ]
    )
    cases.extend(
        [
            make_case(
                name="一般临床问答不应强制命中病例",
                category="长对话·一般临床问答",
                scene="护士临时想问规范性问题，不涉及具体患者",
                ward_summary="系统要像真正的大模型一样回答一般护理问题，不能每次都逼着补床号。",
                patient_points=[
                    "当前病区正在忙，但这个问题本身不针对任何一名患者。",
                ],
                pain_points=[
                    "之前一遇到护理问题就要护士补床号，体验很差。",
                    "老师会现场问一些不带患者上下文的通用问题来试探系统是否灵活。",
                ],
                asks=[
                    "请说明病重护理记录中生命体征的一般记录频次要求。",
                    "补充出入量和病情变化记录时最容易漏掉什么。",
                    "最后给一句适合带教新人护士的提醒。",
                ],
                constraints=[
                    "必须直接作答，不要要求补床号。",
                    "必须出现至少每4小时、出入量、病情变化。",
                ],
                expected_keywords=("至少每4小时", "出入量", "病情变化"),
                require_context_hit=False,
                min_answer_length=200,
            ),
            make_case(
                name="低血压少尿床旁评估顺序",
                category="长对话·一般临床问答",
                scene="护士遇到低血压少尿患者想快速梳理床旁观察重点",
                ward_summary="老师想看系统会不会只说概念，还是能给出床旁先后顺序和升级阈值。",
                patient_points=[
                    "不指定具体患者，只讨论常见场景：低血压、尿量减少、末梢灌注可能变差。",
                ],
                pain_points=[
                    "很多模型会直接说补液观察，却不告诉护士先看什么、什么时候必须报告医生。",
                ],
                asks=[
                    "按床旁第一眼、5 分钟内、30 分钟内三个层次整理观察重点。",
                    "说明哪些情况应立即联系医生。",
                    "补一句适合写进交接班的概括。",
                ],
                constraints=[
                    "不要要求补床号。",
                    "必须出现末梢灌注、尿量、血压趋势、联系医生。",
                ],
                expected_keywords=("末梢灌注", "尿量", "血压趋势", "联系医生"),
                require_context_hit=False,
                min_answer_length=220,
            ),
            make_case(
                name="低氧患者翻身前后观察口径",
                category="长对话·一般临床问答",
                scene="护士想请 AI 说明低氧患者翻身前后应重点看什么",
                ward_summary="这个问题不需要具体患者，但需要临床细节和行动顺序。",
                patient_points=[
                    "常见场景：氧饱和度边缘波动，翻身后容易再次下降。",
                ],
                pain_points=[
                    "老师会看系统能不能说出翻身前准备、翻身中观察、翻身后复评的差别。",
                ],
                asks=[
                    "整理翻身前、中、后的观察重点与风险提示。",
                    "说明什么时候要暂停操作并升级处理。",
                    "写一句适合交接班使用的简洁提醒。",
                ],
                constraints=[
                    "不要要求补床号。",
                    "必须出现氧饱和度、呼吸频率、翻身后复评、暂停并升级处理。",
                ],
                expected_keywords=("氧饱和度", "呼吸频率", "翻身后复评", "升级处理"),
                require_context_hit=False,
                min_answer_length=220,
            ),
            make_case(
                name="压伤高风险患者今日护理重点",
                category="长对话·一般临床问答",
                scene="护士长让系统整理压伤高风险患者今日护理重点",
                ward_summary="回答既要能用于临床，也要适合变成今日待办看板。",
                patient_points=[
                    "常见场景：长期卧床、营养差、皮肤受压点红斑、翻身执行不稳定。",
                ],
                pain_points=[
                    "压伤风险往往被讲成大而空的原则，真正缺的是班次内动作和留痕要点。",
                ],
                asks=[
                    "请整理可直接执行的今日护理重点。",
                    "说明哪些内容应写进交接班，哪些要同步到护理记录。",
                    "补一句适合风险热力图下方显示的提醒。",
                ],
                constraints=[
                    "不要要求补床号。",
                    "必须出现翻身、皮肤观察、营养、交接班、护理记录。",
                ],
                expected_keywords=("翻身", "皮肤观察", "营养", "交接班", "护理记录"),
                require_context_hit=False,
                min_answer_length=220,
            ),
            make_case(
                name="疼痛干预后复评时点与记录",
                category="长对话·一般临床问答",
                scene="护士培训场景，讨论疼痛干预后如何复评",
                ward_summary="老师想看系统是否能把复评时点、体温单疼痛栏和护理记录联动起来。",
                patient_points=[
                    "不指定患者，讨论胃肠外给药和口服镇痛药后复评的常用口径。",
                ],
                pain_points=[
                    "很多回答只会说注意观察，却不说多久复评、如何记录在体温单和护理记录里。",
                ],
                asks=[
                    "说明常见镇痛方式后复评时点。",
                    "解释体温单疼痛栏和护理记录该如何配合。",
                    "给一段适合带教用的操作提醒。",
                ],
                constraints=[
                    "不要要求补床号。",
                    "必须出现15-30分钟、1-2小时、疼痛复评、体温单、护理记录。",
                ],
                expected_keywords=("15-30分钟", "1-2小时", "疼痛复评", "体温单", "护理记录"),
                require_context_hit=False,
                min_answer_length=220,
            ),
            make_case(
                name="导尿与引流观察升级阈值",
                category="长对话·一般临床问答",
                scene="护士想梳理导尿和引流管常见异常的升级阈值",
                ward_summary="这个问题常见但很考验是否真的懂临床护理优先级。",
                patient_points=[
                    "不指定患者，聚焦尿量明显减少、颜色异常、引流不畅和引流液性状变化。",
                ],
                pain_points=[
                    "老师会看系统能不能说清观察点、记录点和什么时候联系医生。",
                ],
                asks=[
                    "整理导尿和引流观察的优先顺序。",
                    "说明哪些异常需要立刻升级。",
                    "给一句交接班里可直接使用的表述。",
                ],
                constraints=[
                    "不要要求补床号。",
                    "必须出现尿量、颜色性状、引流不畅、联系医生、交接班。",
                ],
                expected_keywords=("尿量", "颜色性状", "引流不畅", "联系医生", "交接班"),
                require_context_hit=False,
                min_answer_length=220,
            ),
            make_case(
                name="家属沟通与异常告知口径",
                category="长对话·一般临床问答",
                scene="护士需要一段既稳妥又不空泛的家属沟通建议",
                ward_summary="老师会看系统是否能输出临床上真正能说出口的话，而不是模板化空话。",
                patient_points=[
                    "不针对单一患者，聚焦家属担心低血压、低氧、输血反应和术后恢复时如何沟通。",
                ],
                pain_points=[
                    "家属沟通常常两头难：既不能承诺过度，也不能说得太空。",
                ],
                asks=[
                    "给出一段适合护士使用的沟通框架。",
                    "说明哪些情况需要同步告知医生、哪些内容要留痕。",
                    "补一句适合交接班提醒同事的沟通注意点。",
                ],
                constraints=[
                    "不要要求补床号。",
                    "必须出现沟通、告知、留痕、联系医生、交接班。",
                ],
                expected_keywords=("沟通", "告知", "留痕", "联系医生", "交接班"),
                require_context_hit=False,
                min_answer_length=220,
            ),
            make_case(
                name="AI Agent 记忆机制与连续追踪解释",
                category="长对话·AI Agent记忆",
                scene="老师追问系统如何记住上一轮交代过的风险和待办",
                ward_summary="这不是纯技术说明，老师要看系统能否用临床语言解释记忆如何帮助交接班和持续追踪。",
                patient_points=[
                    "病区里 12床低血压少尿、18床低氧、23床输血、16床高血糖伤口风险是连续多班次问题。",
                ],
                pain_points=[
                    "如果系统记不住上一轮的待办和人工复核，答辩时会显得像一次性聊天机器人。",
                ],
                asks=[
                    "请用临床语言解释 AI Agent 的记忆机制如何帮助连续追踪患者风险和文书状态。",
                    "说明它如何避免把旧信息当成新事件，又如何把多班次重点延续到今日待办和交接班。",
                    "补一句适合放在产品介绍里的短描述。",
                ],
                constraints=[
                    "必须出现记忆、连续追踪、今日待办、交接班、人工复核。",
                    "既要讲清机制，也要贴合临床场景。",
                ],
                expected_keywords=("记忆", "连续追踪", "今日待办", "交接班", "人工复核"),
                min_answer_length=240,
            ),
            make_case(
                name="AI Agent 工作流如何提升效率",
                category="长对话·AI Agent记忆",
                scene="答辩现场，老师追问为什么不是普通大模型而是 AI Agent",
                ward_summary="系统要回答得像产品设计，而不是空泛的技术名词堆砌。",
                patient_points=[
                    "护理场景里既有一般问答，也有病区闭环、文书起草、审核归档和多班次交接。",
                ],
                pain_points=[
                    "如果解释不清 Agent 比普通聊天好在哪里，项目亮点会被削弱。",
                ],
                asks=[
                    "请从病区待办、风险分层、文书草稿、人工审核和归档五个角度解释 AI Agent 的价值。",
                    "说明为什么它比普通问答模型更适合临床护理。",
                    "最后给出一句比赛答辩时可以直接说的话。",
                ],
                constraints=[
                    "必须出现病区待办、风险分层、文书草稿、人工审核、归档。",
                    "不要要求补床号，也不要只讲技术名词。",
                ],
                expected_keywords=("病区待办", "风险分层", "文书草稿", "人工审核", "归档"),
                min_answer_length=240,
            ),
            make_case(
                name="导航首页可视化设计贴临床痛点",
                category="长对话·AI Agent记忆",
                scene="老师希望首页不是信息堆砌，而是一眼看到真正重要的内容",
                ward_summary="系统要说明病区风险热力图、今日待办时间轴和交接班摘要看板为什么切中护理痛点。",
                patient_points=[
                    "病区里同时存在低血压、低氧、输血、血糖波动、跌倒和压伤风险。",
                ],
                pain_points=[
                    "如果首页只是一堆方块卡片，护士会找不到重点，老师也会觉得不够临床。",
                ],
                asks=[
                    "请解释这三个首页可视化各自解决什么临床问题。",
                    "说明护士看完后应该立刻采取什么动作。",
                    "最后补一句适合产品介绍的总结。",
                ],
                constraints=[
                    "必须出现病区风险热力图、今日待办时间轴、交接班摘要看板、优先级、行动。",
                    "回答要明显贴合护理工作流。",
                ],
                expected_keywords=("病区风险热力图", "今日待办时间轴", "交接班摘要看板", "优先级", "行动"),
                min_answer_length=240,
            ),
        ]
    )
    assert len(cases) == 30, len(cases)
    return cases


if __name__ == "__main__":
    raise SystemExit(
        run_suite(
            suite_name="30轮千字级临床 AI Agent 长对话回归",
            suite_id="clinical_long_live30",
            report_filename="clinical_long_dialog_regression_live_30.json",
            cases=build_cases(),
        )
    )
