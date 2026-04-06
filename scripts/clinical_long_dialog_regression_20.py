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
        "请先完整理解下面这些已经发生或正在发生的临床情况，不要把它当成普通聊天问题，而是当成护士在真实班次中一次性交给 AI 的复杂任务。\n"
        "重点患者与现状如下：\n"
        + "\n".join(f"{idx + 1}. {item}" for idx, item in enumerate(patient_points))
        + "\n当前最让我头疼、也是最容易出错的临床痛点有：\n"
        + "\n".join(f"- {item}" for item in pain_points)
        + "\n另外这不是单纯答题场景，而是面向护士长、带教老师和责任护士的真实演示场景：他们会同时看你能不能把床旁观察、复测安排、医嘱执行、医生沟通、家属沟通、风险分层、交接承接和人工复核串成一条闭环，还会追问哪些是客观事实、哪些需要马上复测、哪些必须升级汇报、哪些只能暂列待确认。"
        + "\n所以请把每一条建议都尽量落到临床动作、观察重点、复核时间点、交接留痕和升级阈值，不要只给原则性描述，也不要回避谁先做、做到什么程度、什么情况下必须联系医生或再次上报。"
        + "\n我希望你这次不要泛泛讲原则，而是像真正能落地的临床 AI Agent 一样，按工作流帮我完成以下事情：\n"
        + "\n".join(f"{idx + 1}. {item}" for idx, item in enumerate(asks))
        + "\n回答时必须严格满足这些要求：\n"
        + "\n".join(f"- {item}" for item in constraints)
        + "\n如果你要生成交班、护理记录或文书草稿，请尽量使用临床护理常用表达，体现先后顺序、观察重点、复核时间点、联系医生阈值、护士人工确认内容、交班留痕和提交前复核逻辑。"
    )


def build_cases() -> list[RegressionCase]:
    return [
        RegressionCase(
            name="晨间巡检到交班文书闭环",
            category="长对话-AI Agent",
            mode="agent_cluster",
            execution_profile="full_loop",
            cluster_profile="nursing_default_cluster",
            user_input=build_long_prompt(
                scene="心内科普通病区白班晨间巡检前 30 分钟",
                ward_summary="病区今天床位紧张，夜班交上来的信息比较碎，护士长要求白班一上来先看高危患者，再补齐文书，最后把重点交班逻辑梳理出来，避免只顾着抢时间却漏掉真正危险的点。",
                patient_points=[
                    "12床男性，扩容治疗后收缩压仍在 88-92 mmHg 波动，尿量偏少，昨晚有液体出入量记录不完整的问题，夜班口头提到要继续复测血压和尿量，但书面留痕不够完整。",
                    "18床慢阻肺合并低氧风险，夜班最低血氧到过 89%，吸氧后回到 92%-93%，呼吸频率 26-28 次/分波动，护士担心下一轮巡视前又掉下去。",
                    "23床感染合并贫血，清晨体温 37.9℃，面色差，昨天下午医生提到要关注输血前评估和感染控制，但输血护理记录和一般护理记录都还没有形成统一口径。",
                    "16床糖尿病足合并感染扩散风险，随机血糖高，伤口处理后还要补血糖相关记录和交班提醒。",
                ],
                pain_points=[
                    "病区信息来源混杂，容易先处理手头最急的事，却漏掉交班、医生沟通和文书之间应该互相对应的内容。",
                    "护士常常知道哪个床危险，但不容易在短时间内把优先级、文书草稿、人工复核点和下一班提醒整成一个闭环。",
                    "老师看演示时最关注的是：这个系统是不是真的像护士长一样，能把病区工作串起来，而不是只会回答一句建议。",
                ],
                asks=[
                    "先帮我按白班晨间巡检顺序排出最先看、随后看和持续追踪的床位，并说明排序理由。",
                    "把需要先联系医生、可以先床旁复核后再决定、以及可以先由 AI 起草文书的事项区分开来。",
                    "生成一份病区级交班与文书闭环执行清单，至少体现交班草稿、一般护理记录、体温单或输血护理记录的优先处理方向。",
                    "明确告诉我哪些信息必须由护士人工确认，哪些部分可以由 AI Agent 先起草再交给护士修改。"
                ],
                constraints=[
                    "不要要求我再补床号，因为这是病区任务。",
                    "输出里要出现优先级、交班草稿、文书草稿、人工确认、提交前复核这些关键词。",
                    "回答要像真正交给责任护士执行的清单，而不是空泛总结。",
                ],
            ),
            expect_workflows=("autonomous_care",),
            expected_keywords=("优先", "交班草稿", "文书草稿", "人工确认", "复核"),
            expect_artifact_kinds=("handover_batch", "document_plan"),
            min_answer_length=260,
            min_prompt_length=700,
            require_context_hit=True,
            max_elapsed_sec=90,
        ),
        RegressionCase(
            name="夜班双护士高危分工闭环",
            category="长对话-AI Agent",
            mode="agent_cluster",
            execution_profile="full_loop",
            cluster_profile="nursing_default_cluster",
            user_input=build_long_prompt(
                scene="普外科夜班只剩两名护士值班的时段",
                ward_summary="现在病区人手很紧，夜班护士需要在不增加太多认知负担的前提下，快速判断谁必须马上处理，谁可以 30 分钟后处理，谁只需要带着任务交给下一班继续盯。",
                patient_points=[
                    "17床术后切口疼痛评分 7 分，夜班已经用药但还没到复评时间，切口渗液不多但护士担心后半夜会加重。",
                    "18床低氧风险高，氧疗后仍有波动，床旁护士说他一翻身就喘得厉害，担心后面还要再通知医生。",
                    "12床低血压和少尿趋势还在，夜班已补液但回血压不理想，值班医生昨晚交代要盯住尿量和末梢灌注。",
                    "20床老年患者跌倒高风险，情绪烦躁，反复想下床上厕所，床旁陪护不稳定。",
                ],
                pain_points=[
                    "夜班最怕的不是事情多，而是优先级排序错了，导致真正危险的床位被耽误。",
                    "交班时常常只说了表面异常，没有形成谁负责、何时复核、何时升级报告的闭环。",
                    "临床上非常需要一种‘双护士分工版’的 AI Agent 输出，而不是一堆原则。"
                ],
                asks=[
                    "按只能分配两名护士的现实条件，给出一份夜班分工和床旁调度顺序。",
                    "把必须马上处理、30 分钟内处理、可以带任务交下一班处理三层写清楚，并说明分类依据。",
                    "写出每一床需要重点观察的危险信号，以及达到什么阈值必须马上联系医生。",
                    "最后给下一班形成可执行的闭环任务清单。"
                ],
                constraints=[
                    "必须体现双护士分工。",
                    "必须出现马上处理、30分钟内处理、下一班、联系医生这些词。",
                    "输出要能直接给夜班护士照着执行。",
                ],
            ),
            expect_workflows=("autonomous_care",),
            expected_keywords=("马上处理", "30分钟内处理", "下一班", "联系医生"),
            min_answer_length=240,
            min_prompt_length=680,
            require_context_hit=True,
            max_elapsed_sec=90,
        ),
        RegressionCase(
            name="交班前总复盘与一致性检查",
            category="长对话-AI Agent",
            mode="agent_cluster",
            execution_profile="full_loop",
            user_input=build_long_prompt(
                scene="下午交班前最后 40 分钟",
                ward_summary="病区今天事情很多，护士担心交班报告、一般护理记录和当天已经口头通知医生的内容彼此前后不一致，老师如果抽问，就会暴露出系统只是会生成文字，不会真正做一致性复核。",
                patient_points=[
                    "12床上午两次低血压波动，已有一次电话汇报医生，补液后略有回升，但尿量趋势还需要继续看。",
                    "18床今天一直处在低氧边缘，吸氧效果一般，下午护士已经床旁复核过一次生命体征。",
                    "23床拟做输血前评估，感染和贫血两条线都要在交班里说清楚。",
                    "16床血糖波动和伤口处理需要在护理记录与交班中对应起来。",
                ],
                pain_points=[
                    "口头汇报过医生但文书没补齐，是临床最常见也最危险的断点之一。",
                    "交班常常只讲风险，不讲已经做了什么、下一班还需要看什么。",
                    "比赛演示时如果老师追问‘你怎么保证记录和交班一致’，系统必须能答得非常实用。"
                ],
                asks=[
                    "做一份交班前总复盘，指出哪些患者还需要再次复核。",
                    "指出哪些文书缺关键字段，哪些医生沟通还需要补记。",
                    "说明怎样让交班报告、一般护理记录和重点医护沟通内容保持一致。",
                    "给出一份提交前复核清单。"
                ],
                constraints=[
                    "必须出现一致、关键字段、提交前复核、下一班。",
                    "不要只说原则，要说临床上真正要核对的项目。",
                ],
            ),
            expect_workflows=("autonomous_care",),
            expected_keywords=("一致", "关键字段", "提交前复核", "下一班"),
            min_answer_length=230,
            min_prompt_length=650,
            require_context_hit=True,
            max_elapsed_sec=90,
        ),
        RegressionCase(
            name="多床医生汇总与调度顺序",
            category="长对话-AI Agent",
            mode="agent_cluster",
            execution_profile="full_loop",
            user_input=build_long_prompt(
                scene="值班医生准备集中电话汇报前",
                ward_summary="临床上最怕的就是多床同时有问题时，护士一紧张就想到什么说什么，没有先后顺序，也说不清到底为什么这个床要先上报。",
                patient_points=[
                    "12床低血压、少尿，需要看补液效果和是否进一步恶化。",
                    "18床低氧、呼吸频率偏快，需要判断氧疗后是否仍在恶化。",
                    "23床感染贫血，输血前评估和交班内容需要统一。",
                    "20床跌倒高风险、情绪烦躁，需要持续看护。",
                ],
                pain_points=[
                    "多床并发时，最重要的是排序、床旁动作、医生沟通话术和文书衔接必须统一。",
                    "护士不缺原则，缺的是一份临床可复制的汇报顺序和一句话脚本。",
                ],
                asks=[
                    "请按风险排序整理值班医生汇总顺序。",
                    "每床给一句电话汇报话术，体现最核心异常和希望医生决策的点。",
                    "告诉我哪些床先床旁复核再汇报，哪些床必须立即电话汇报。",
                    "再附一份交班和护理记录如何同步更新的提醒。"
                ],
                constraints=[
                    "要出现排序、电话、联系医生、交班这些词。",
                    "输出必须适合护士临场直接照着说。",
                ],
            ),
            expect_workflows=("autonomous_care",),
            expected_keywords=("排序", "电话", "联系医生", "交班"),
            min_answer_length=220,
            min_prompt_length=620,
            require_context_hit=True,
            max_elapsed_sec=90,
        ),
        RegressionCase(
            name="输血护理记录与交班联动长对话",
            category="长对话-文书",
            mode="agent_cluster",
            execution_profile="agent",
            user_input=build_long_prompt(
                scene="内科病区下午准备输血前后交班",
                ward_summary="护士需要把输血前评估、双人核对、开始时间、15 分钟观察、结束后 60 分钟内复评、是否有不良反应这些点写得又规范又好懂，还要和下一班交代一致。",
                patient_points=[
                    "23床男性，A+ 型，既往有感染与贫血背景，今天准备输注红细胞 1 袋，医生已口头交代注意发热和寒战反应。",
                    "患者本人比较紧张，家属会频繁追问是否会有反应，护士需要把观察点说清楚并且记录留痕。",
                    "当前病区比较忙，老师演示时很可能会追问‘输血护理记录最关键的时间点是什么、怎样和交班一致’。"
                ],
                pain_points=[
                    "输血护理记录最怕遗漏开始时间、15 分钟复核和结束后再评估。",
                    "有些系统会只生成文案，不会提示双人核对、异常反应观察和交班提醒。",
                ],
                asks=[
                    "先生成一份 23 床输血护理记录草稿思路，体现关键时间点和双人核对。",
                    "再把这些内容转换成适合下一班接手的交班要点。",
                    "说明哪些字段必须护士人工补写，哪些部分可以先由 AI 起草。",
                ],
                constraints=[
                    "要出现输血护理记录、双人核对、15分钟、结束后60分钟、交班。",
                    "回答必须贴合临床书写规范。",
                ],
            ),
            expect_workflows=("document_generation", "handover_generate"),
            expected_keywords=("输血护理记录", "双人核对", "15分钟", "60分钟", "交班"),
            min_answer_length=220,
            min_prompt_length=620,
            max_elapsed_sec=80,
        ),
        RegressionCase(
            name="体温单发热补录长对话",
            category="长对话-文书",
            mode="agent_cluster",
            execution_profile="agent",
            user_input=build_long_prompt(
                scene="发热患者体温单补录与护理记录联动场景",
                ward_summary="体温单最容易出错的地方是常规测量、发热复测、降温后红圈虚线连接以及护理记录里补记变化情况，很多人知道规则却写不整齐。",
                patient_points=[
                    "16床今日 15:00 体温 38.4℃，18:50 复测 38.1℃，采取物理降温后 20:00 体温 37.6℃，护士还要继续看夜间体温变化。",
                    "患者同时合并感染扩散风险，下一班需要知道什么时候改回常规测量，什么时候必须继续 4 小时测一次。",
                ],
                pain_points=[
                    "系统如果只会说‘继续观察’，对体温单书写帮助几乎为零。",
                    "临床真正需要的是把体温单绘制规则和护理记录补记点一起串起来。",
                ],
                asks=[
                    "先告诉我这类体温单补录最关键的规则和时间点。",
                    "再帮我起一个体温单电子录入草稿思路，体现发热复测和降温后的记录方式。",
                    "最后给下一班一段交接提醒。"
                ],
                constraints=[
                    "必须出现体温单、复测、红圈、虚线、下一班。",
                    "不能只说原则，要体现怎么记录。",
                ],
            ),
            expect_workflows=("document_generation",),
            expected_keywords=("体温单", "复测", "红圈", "虚线", "下一班"),
            min_answer_length=200,
            min_prompt_length=560,
            max_elapsed_sec=80,
        ),
        RegressionCase(
            name="病重护理记录长对话",
            category="长对话-文书",
            mode="agent_cluster",
            execution_profile="agent",
            user_input=build_long_prompt(
                scene="病重患者护理记录补写场景",
                ward_summary="老师通常会追问病重护理记录到底要记录什么频次、哪些客观指标、哪些病情变化和护理措施必须写进去，系统要能说得像真正的病区老师带教。",
                patient_points=[
                    "12床持续低血压伴少尿，值班护士已经做过扩容和床旁观察，但病重护理记录里还缺生命体征频次、出入量和效果评价。",
                    "患者上午有一次意识反应差、末梢偏凉，后续略有改善，但需要在护理记录里体现连续性。",
                ],
                pain_points=[
                    "最常见问题是只记录病情，不写护理措施和效果。",
                    "另一个常见问题是只写文字，不写到分钟、不体现连续性。"
                ],
                asks=[
                    "请生成病重患者护理记录的完整起草思路。",
                    "说明生命体征、出入量、病情观察、护理措施和效果评价各自该怎么体现。",
                    "再给一份交班时最应该说出去的重点。"
                ],
                constraints=[
                    "必须出现病重护理记录、生命体征、出入量、护理措施、效果。",
                    "要贴合临床记录规范。",
                ],
            ),
            expect_workflows=("document_generation", "handover_generate"),
            expected_keywords=("病重护理记录", "生命体征", "出入量", "护理措施", "效果"),
            min_answer_length=220,
            min_prompt_length=560,
            max_elapsed_sec=80,
        ),
        RegressionCase(
            name="血糖谱与POCT联动长对话",
            category="长对话-文书",
            mode="agent_cluster",
            execution_profile="agent",
            user_input=build_long_prompt(
                scene="内分泌合并外科病区晚间血糖管理",
                ward_summary="护士需要同时考虑血糖谱、临时追加的随机血糖与血酮体、POCT 记录、下一班观察和伤口感染风险，不能把它拆成孤立问题。",
                patient_points=[
                    "16床糖尿病足合并感染，早餐前、午餐前、晚餐前和睡前都需要关注血糖谱，夜间又追加一次随机血糖复测。",
                    "患者伤口情况变化较快，老师演示时很可能会追问 POCT 记录单和交班里到底怎么对应。",
                ],
                pain_points=[
                    "很多系统只会回答‘监测血糖’，但临床真正需要的是记录格式、追加测量项目和交班重点。",
                ],
                asks=[
                    "请把血糖测量记录单、血糖谱记录和追加随机血糖/血酮体记录的要点串起来。",
                    "告诉我哪些内容适合 AI 先起草，哪些必须护士补写。",
                    "最后给一段下一班监测重点。"
                ],
                constraints=[
                    "要出现血糖测量记录单、血糖谱、POCT、复测、下一班。",
                ],
            ),
            expect_workflows=("document_generation",),
            expected_keywords=("血糖测量记录单", "血糖谱", "POCT", "复测", "下一班"),
            min_answer_length=200,
            min_prompt_length=520,
            max_elapsed_sec=80,
        ),
        RegressionCase(
            name="呼吸机夜班带教长对话",
            category="长对话-通用临床",
            mode="single_model",
            execution_profile="single_model",
            cluster_profile=None,
            user_input=build_long_prompt(
                scene="ICU 夜班带教语境",
                ward_summary="我不是要你泛泛解释呼吸机原理，而是要你像带教老师一样，按夜班护士真正会遇到的情况，把报警、低氧、气道分泌物、体位变化、镇静评估和必须联系医生的阈值讲清楚。",
                patient_points=[
                    "患者有创呼吸机辅助通气，夜班一翻身就容易血氧掉，偶尔有高压报警和分泌物增多。",
                    "新护士经常一听到报警就慌，不知道先看病人还是先看机器参数，也不知道哪些情况必须马上喊医生。",
                ],
                pain_points=[
                    "临床上最重要的是先后顺序和危险阈值，不是概念定义。",
                ],
                asks=[
                    "请按夜班值班实际顺序讲：报警后先看什么，再查什么，再怎么处理。",
                    "把哪些情况必须马上联系医生说清楚。",
                    "最后给一段适合交给下一班的提醒。"
                ],
                constraints=[
                    "要出现报警、血氧、分泌物、联系医生、下一班。",
                ],
            ),
            expect_workflows=("single_model_chat",),
            expected_keywords=("报警", "血氧", "分泌物", "联系医生", "下一班"),
            min_answer_length=220,
            min_prompt_length=520,
            require_context_hit=False,
            max_elapsed_sec=50,
        ),
        RegressionCase(
            name="低血压少尿处理长对话",
            category="长对话-专业建议",
            mode="agent_cluster",
            execution_profile="agent",
            user_input=build_long_prompt(
                scene="心内科低血压少尿患者床旁评估",
                ward_summary="我需要的是护士现在就能做的床旁动作排序，以及哪些情况一定不能继续拖，要马上找医生，不是泛泛一句‘继续观察’。",
                patient_points=[
                    "12床扩容后收缩压仍在 88-92 mmHg，尿量偏少，四肢偏凉，患者自诉头晕，夜班已记录一次出入量但不完整。",
                ],
                pain_points=[
                    "护士最需要的是复核顺序、观察重点、再评估时间和升级阈值。",
                ],
                asks=[
                    "请把床旁先后顺序写清楚。",
                    "明确哪些体征提示需要马上联系医生。",
                    "告诉我护理记录和交班里各自最该写什么。"
                ],
                constraints=[
                    "要出现尿量、再评估、联系医生、护理记录、交班。",
                ],
            ),
            expect_workflows=("recommendation_request",),
            expected_keywords=("尿量", "再评估", "联系医生", "护理记录", "交班"),
            min_answer_length=180,
            min_prompt_length=420,
            max_elapsed_sec=60,
        ),
        RegressionCase(
            name="腹泻脱水补液平衡长对话",
            category="长对话-专业建议",
            mode="agent_cluster",
            execution_profile="agent",
            user_input=build_long_prompt(
                scene="消化科腹泻伴液体丢失评估",
                ward_summary="现在我希望你把补液平衡、腹痛观察、尿量变化、再次联系医生阈值和下一班交代一次性讲透，像真正病区带教那样。",
                patient_points=[
                    "19床频繁腹泻后口干、乏力，心率偏快，尿量减少，值班护士担心液体丢失还会继续扩大。",
                ],
                pain_points=[
                    "这类问题如果只说补液或继续观察，临床价值很差。",
                ],
                asks=[
                    "先说床旁观察重点。",
                    "再说补液平衡和出入量最该怎么盯。",
                    "最后说何时必须再次联系医生，并怎么交代给下一班。"
                ],
                constraints=[
                    "要出现出入量、尿量、联系医生、下一班。",
                ],
            ),
            expect_workflows=("recommendation_request",),
            expected_keywords=("出入量", "尿量", "联系医生", "下一班"),
            min_answer_length=170,
            min_prompt_length=400,
            max_elapsed_sec=60,
        ),
        RegressionCase(
            name="中医护理辨证长对话",
            category="长对话-中医护理",
            mode="agent_cluster",
            execution_profile="agent",
            user_input=build_long_prompt(
                scene="中西医结合病区晨间带教",
                ward_summary="我希望你不要把中医护理讲成空话，而是结合护士能观察到的症状、饮食、睡眠、情绪和舌脉线索，补充一份真正能用于护理观察和交班的中医护理要点。",
                patient_points=[
                    "12床长期乏力、面色少华、纳差，夜间睡眠浅，舌淡，值班护士希望从中医角度补充护理观察线索。",
                    "18床咳喘、痰多、焦虑，夜间稍有烦躁，护士希望知道中医护理能补充哪些观察和情志护理点。",
                ],
                pain_points=[
                    "很多系统只会给中医名词，护士却不知道要看什么、怎么交班、何时要转医生。",
                ],
                asks=[
                    "请补充中医辨证线索。",
                    "把护理观察重点、饮食调护和情志护理说清楚。",
                    "告诉我何时必须转医生处理。"
                ],
                constraints=[
                    "要出现证候、饮食、情志、联系医生。",
                ],
            ),
            expect_workflows=("recommendation_request", "voice_inquiry"),
            expected_keywords=("证候", "饮食", "情志", "联系医生"),
            min_answer_length=180,
            min_prompt_length=440,
            max_elapsed_sec=60,
        ),
        RegressionCase(
            name="护士长总览版闭环长对话",
            category="长对话-AI Agent",
            mode="agent_cluster",
            execution_profile="full_loop",
            user_input=build_long_prompt(
                scene="护士长查房前总览场景",
                ward_summary="护士长不是只想知道谁高危，更想知道哪些事项已经闭环、哪些文书还没补齐、哪些医生沟通还没落地，以及哪些地方仍然要人工确认。",
                patient_points=[
                    "病区同时存在低血压、低氧、贫血感染、血糖波动和跌倒高风险患者。",
                    "部分患者已经口头汇报医生，部分患者已经有草稿文书，部分则还停留在口头交代阶段。",
                ],
                pain_points=[
                    "老师很容易问：如果你是护士长，你怎么看全病区风险和未闭环事项？",
                ],
                asks=[
                    "请整理成护士长总览版。",
                    "明确已闭环和未闭环事项。",
                    "标出仍需人工确认的地方。",
                ],
                constraints=[
                    "要出现护士长、闭环、人工确认。",
                ],
            ),
            expect_workflows=("autonomous_care",),
            expected_keywords=("护士长", "闭环", "人工确认"),
            min_answer_length=200,
            min_prompt_length=420,
            require_context_hit=True,
            max_elapsed_sec=90,
        ),
        RegressionCase(
            name="责任护士执行版长对话",
            category="长对话-AI Agent",
            mode="agent_cluster",
            execution_profile="full_loop",
            user_input=build_long_prompt(
                scene="责任护士班中执行版场景",
                ward_summary="这次我不需要给护士长看的概览，而是要给责任护士一份今天这一班可以直接照着做的执行版闭环任务单，最好能从先看哪张床开始一路排到文书留痕和交班收尾。",
                patient_points=[
                    "12床低血压少尿需要反复评估，18床低氧需要持续盯，23床输血与感染风险交织。",
                ],
                pain_points=[
                    "责任护士需要的是执行顺序，不是总结。",
                ],
                asks=[
                    "给我一份责任护士执行版清单。",
                    "写清先后顺序、复核节点、联系医生时机、留痕与交班收尾。",
                ],
                constraints=[
                    "要出现责任护士、执行版、留痕、联系医生。",
                ],
            ),
            expect_workflows=("autonomous_care",),
            expected_keywords=("责任护士", "执行版", "留痕", "联系医生"),
            min_answer_length=200,
            min_prompt_length=380,
            require_context_hit=True,
            max_elapsed_sec=90,
        ),
        RegressionCase(
            name="交班后持续追踪长对话",
            category="长对话-AI Agent",
            mode="agent_cluster",
            execution_profile="full_loop",
            user_input=build_long_prompt(
                scene="交班后 1 小时复盘场景",
                ward_summary="交班结束不代表事情结束，很多异常需要二次复核、文书补改和再次联系医生。我要看的是系统能不能像真正的 AI Agent 一样继续追踪，而不是交班完就结束。",
                patient_points=[
                    "12床低血压问题还未完全回稳。",
                    "18床低氧在活动后可能再次波动。",
                    "23床输血相关记录和感染观察还需要继续跟。",
                ],
                pain_points=[
                    "临床最怕‘交完班就断档’，没有追踪清单。",
                ],
                asks=[
                    "给我一份交班后持续追踪任务清单。",
                    "指出哪些患者需要二次复核、哪些文书需要补改、哪些异常需要再次联系医生。",
                ],
                constraints=[
                    "要出现二次复核、补改、联系医生、持续闭环。",
                ],
            ),
            expect_workflows=("autonomous_care",),
            expected_keywords=("二次复核", "补改", "联系医生", "持续闭环"),
            min_answer_length=200,
            min_prompt_length=400,
            require_context_hit=True,
            max_elapsed_sec=90,
        ),
        RegressionCase(
            name="跌倒高风险夜间看护长对话",
            category="长对话-通用临床",
            mode="single_model",
            execution_profile="single_model",
            cluster_profile=None,
            user_input=build_long_prompt(
                scene="夜班跌倒高风险患者管理",
                ward_summary="请按夜班临床实际顺序说明，不要只是背诵跌倒预防原则。",
                patient_points=[
                    "20床高龄、情绪烦躁、反复想下床去厕所，陪护不稳定，床旁有输液管路。",
                ],
                pain_points=[
                    "夜班最担心的就是如厕、翻身和临时下床这几个节点。",
                ],
                asks=[
                    "告诉我环境管理、如厕陪护、起身协助和管路整理的先后顺序。",
                    "说清楚交班时最应该提醒下一班什么。"
                ],
                constraints=[
                    "要出现如厕、陪护、交班。",
                ],
            ),
            expect_workflows=("single_model_chat",),
            expected_keywords=("如厕", "陪护", "交班"),
            min_answer_length=160,
            min_prompt_length=300,
            require_context_hit=None,
            max_elapsed_sec=50,
        ),
        RegressionCase(
            name="压伤高风险带教长对话",
            category="长对话-通用临床",
            mode="single_model",
            execution_profile="single_model",
            cluster_profile=None,
            user_input=build_long_prompt(
                scene="压伤高风险预防带教场景",
                ward_summary="请按临床护理常规回答，重点不是定义压伤，而是护士到底应该怎样执行翻身减压、皮肤观察、湿性管理、营养观察和升级处理。",
                patient_points=[
                    "卧床患者翻身依从性差，骶尾部皮肤发红，营养一般，大小便失禁间断存在。",
                ],
                pain_points=[
                    "系统最容易答成教科书，但病区真正需要的是执行要点。",
                ],
                asks=[
                    "按先后顺序说出具体护理动作。",
                    "说明何时需要联系医生。"
                ],
                constraints=[
                    "要出现翻身、皮肤、湿性、联系医生。",
                ],
            ),
            expect_workflows=("single_model_chat",),
            expected_keywords=("翻身", "皮肤", "湿性", "联系医生"),
            min_answer_length=170,
            min_prompt_length=320,
            require_context_hit=False,
            max_elapsed_sec=50,
        ),
        RegressionCase(
            name="导尿管异常长对话",
            category="长对话-专业建议",
            mode="agent_cluster",
            execution_profile="agent",
            user_input=build_long_prompt(
                scene="留置导尿患者异常评估",
                ward_summary="请像床旁带教一样回答：我需要的是先后顺序，不是概念。",
                patient_points=[
                    "患者留置导尿后突然尿量减少，下腹不适，尿液混浊，护士担心导尿管堵塞或感染。",
                ],
                pain_points=[
                    "最怕不知道先看管道还是先看病人。",
                ],
                asks=[
                    "按床旁先后顺序给出观察和处理步骤。",
                    "说清楚何时联系医生，护理记录里要补什么。"
                ],
                constraints=[
                    "要出现导尿管、尿量、联系医生、护理记录。",
                ],
            ),
            expect_workflows=("recommendation_request",),
            expected_keywords=("导尿管", "尿量", "联系医生", "护理记录"),
            min_answer_length=170,
            min_prompt_length=320,
            max_elapsed_sec=60,
        ),
        RegressionCase(
            name="术后引流异常长对话",
            category="长对话-专业建议",
            mode="agent_cluster",
            execution_profile="agent",
            user_input=build_long_prompt(
                scene="术后引流观察场景",
                ward_summary="请把引流量、颜色、切口情况、何时联系医生和如何交班一次说清楚。",
                patient_points=[
                    "术后患者引流液突然偏鲜红，量较前增加，护士担心是否出血或引流异常。",
                ],
                pain_points=[
                    "系统最容易只给一句‘继续观察’，但临床需要的是升级阈值。",
                ],
                asks=[
                    "说明床旁观察顺序。",
                    "说明何时立即联系医生。",
                    "给一段适合下一班的交班提醒。"
                ],
                constraints=[
                    "要出现引流、鲜红、联系医生、交班。",
                ],
            ),
            expect_workflows=("recommendation_request",),
            expected_keywords=("引流", "鲜红", "联系医生", "交班"),
            min_answer_length=170,
            min_prompt_length=320,
            max_elapsed_sec=60,
        ),
        RegressionCase(
            name="病区文书协同长对话",
            category="长对话-AI Agent",
            mode="agent_cluster",
            execution_profile="full_loop",
            user_input=build_long_prompt(
                scene="病区下午集中补文书时段",
                ward_summary="我要看的不是单床，而是病区在忙的时候，AI 能不能帮护士把体温单、一般护理记录、输血护理记录和交班草稿这些工作先做分层排序，再把人工确认边界讲清楚。",
                patient_points=[
                    "病区里同时存在需要补体温单的发热患者、需要补一般护理记录的病重患者、需要输血护理记录的贫血患者和需要交班草稿的高风险患者。",
                ],
                pain_points=[
                    "文书多的时候最怕乱找患者、乱找草稿、先后顺序不清。",
                ],
                asks=[
                    "按病区优先级做一份文书协同清单。",
                    "区分 AI 可先起草和护士必须人工确认的部分。"
                ],
                constraints=[
                    "要出现文书、体温单、一般护理记录、输血护理记录、人工确认。",
                ],
            ),
            expect_workflows=("autonomous_care",),
            expected_keywords=("文书", "体温单", "一般护理记录", "输血护理记录", "人工确认"),
            expect_artifact_kinds=("document_plan",),
            min_answer_length=210,
            min_prompt_length=360,
            require_context_hit=True,
            max_elapsed_sec=90,
        ),
    ]


if __name__ == "__main__":
    raise SystemExit(
        run_suite(
            suite_name="20条千字级临床长对话回归",
            suite_id="clinical_long20",
            report_filename="clinical_long_dialog_regression_20.json",
            cases=build_cases(),
        )
    )
