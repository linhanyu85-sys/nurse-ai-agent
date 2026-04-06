from __future__ import annotations

from clinical_regression_common import RegressionCase, run_suite


def single_case(name: str, prompt: str, *keywords: str, min_len: int = 120) -> RegressionCase:
    return RegressionCase(
        name=name,
        category="普通临床问答",
        mode="single_model",
        execution_profile="single_model",
        cluster_profile=None,
        user_input=prompt,
        expect_workflows=("single_model_chat",),
        expected_keywords=keywords,
        min_answer_length=min_len,
        require_context_hit=False,
        max_elapsed_sec=50,
    )


def recommendation_case(name: str, prompt: str, *keywords: str, min_len: int = 140, context_hit: bool | None = None) -> RegressionCase:
    return RegressionCase(
        name=name,
        category="专业建议",
        mode="agent_cluster",
        execution_profile="agent",
        user_input=prompt,
        expect_workflows=("recommendation_request",),
        expected_keywords=keywords,
        min_answer_length=min_len,
        require_context_hit=context_hit,
        max_elapsed_sec=60,
    )


def handover_case(name: str, prompt: str, *keywords: str, min_len: int = 140) -> RegressionCase:
    return RegressionCase(
        name=name,
        category="交班与文书",
        mode="agent_cluster",
        execution_profile="agent",
        user_input=prompt,
        expect_workflows=("handover_generate",),
        expected_keywords=keywords,
        min_answer_length=min_len,
        max_elapsed_sec=70,
    )


def document_case(name: str, prompt: str, *keywords: str, min_len: int = 150) -> RegressionCase:
    return RegressionCase(
        name=name,
        category="交班与文书",
        mode="agent_cluster",
        execution_profile="agent",
        user_input=prompt,
        expect_workflows=("document_generation",),
        expected_keywords=keywords,
        min_answer_length=min_len,
        max_elapsed_sec=70,
    )


def autonomous_case(
    name: str,
    prompt: str,
    *keywords: str,
    artifacts: tuple[str, ...] = (),
    min_len: int = 180,
) -> RegressionCase:
    return RegressionCase(
        name=name,
        category="AI Agent任务",
        mode="agent_cluster",
        execution_profile="full_loop",
        user_input=prompt,
        expect_workflows=("autonomous_care",),
        expected_keywords=keywords,
        expect_artifact_kinds=artifacts,
        min_answer_length=min_len,
        require_context_hit=True,
        max_elapsed_sec=90,
    )


def build_cases() -> list[RegressionCase]:
    cases: list[RegressionCase] = [
        single_case("高流量氧疗夜班观察", "请按夜班床旁护理顺序回答：高流量吸氧患者夜里血氧波动时先看什么、再看什么、什么情况下必须马上联系医生，并且交班时要提醒下一班盯住哪些点？", "血氧", "联系医生", "下一班"),
        single_case("低血糖口服与静脉补糖切换", "低血糖患者先口服补糖还是直接静脉补糖，复测间隔、观察重点和升级处理怎么区分？请按床旁顺序讲清楚。", "复测", "补糖", "升级"),
        single_case("跌倒高风险夜班看护", "夜班只有两名护士时，高跌倒风险患者在如厕、起身、翻身和输液管路管理上应怎样排优先级？", "如厕", "起身", "管路"),
        single_case("压伤高风险预防", "压伤高风险患者的翻身减压、皮肤观察、潮湿失禁管理和联系医生阈值分别怎么做？", "翻身", "皮肤", "联系医生"),
        single_case("导尿管异常处理", "留置导尿患者尿量突然减少、尿液混浊并有下腹不适时，护士先看什么、先做什么、何时联系医生？", "导尿管", "尿量", "联系医生"),
        single_case("输液外渗处置", "输液外渗时第一时间怎么处理、局部观察什么、何时上报医生、护理记录必须写什么？", "外渗", "观察", "护理记录"),
        single_case("术后引流观察", "术后引流液突然变鲜红或量增加时，护士床旁先看什么、什么情况下必须马上联系医生？", "引流", "鲜红", "联系医生"),
        single_case("疼痛复评要点", "镇痛处理后疼痛复评最关键的时间点和记录要点是什么？", "疼痛", "复评", "记录"),
        single_case("呼吸机报警先后顺序", "呼吸机报警时先看患者还是先看机器？请按床旁先后顺序回答。", "报警", "患者", "机器"),
        single_case("术后早期活动带教", "术后患者第一次坐起、站立、下床活动时，护士最该先看什么、先防什么风险？", "坐起", "站立", "风险"),
        single_case("中医护理观察点", "从中医护理角度看，纳差、乏力、睡眠差的患者，护士可以重点观察哪些证候线索、饮食和情志变化？", "证候", "饮食", "情志"),
        single_case("护理文书种类说明", "护士日常最常写的护理文书有哪些，它们各自最关键的书写重点是什么？", "护理文书", "体温单", "交接班"),

        recommendation_case("12床低血压少尿", "请总结12床当前病情重点，特别关注收缩压 88 mmHg 和尿量偏少时护士先做什么、何时重新评估、何时联系医生。", "尿量", "重新评估", "联系医生", context_hit=True),
        recommendation_case("18床低氧处理", "18床血氧 89%-93% 波动、呼吸频率偏快，请告诉我先复核什么、观察什么、达到什么阈值联系医生。", "血氧", "呼吸频率", "联系医生", context_hit=True),
        recommendation_case("23床感染贫血输血前评估", "23床感染合并贫血，准备输血前护士要先评估什么、先补什么记录、哪些异常要先汇报医生？", "输血", "评估", "医生", context_hit=True),
        recommendation_case("16床高血糖与感染", "16床血糖高又有感染风险，床旁先看什么、复测什么、怎么交代下一班？", "血糖", "复测", "下一班", context_hit=True),
        recommendation_case("20床跌倒烦躁", "20床夜间烦躁反复想下床，高跌倒风险时本班最优先的安全动作和观察点是什么？", "跌倒", "观察", "安全", context_hit=True),
        recommendation_case("17床疼痛切口", "17床术后疼痛评分高、切口感染风险在上升，护士先处理什么、何时复评、何时联系医生？", "疼痛", "复评", "联系医生", context_hit=True),
        recommendation_case("腹泻脱水补液平衡", "腹泻伴液体丢失患者，护士如何看出入量、尿量和腹痛变化，什么情况必须再次联系医生？", "出入量", "尿量", "联系医生"),
        recommendation_case("导尿堵塞与感染", "导尿患者疑似堵塞又担心感染时，护士先核对什么、处理什么、什么时候别再等直接找医生？", "导尿管", "感染", "找医生"),
        recommendation_case("引流鲜红增多", "术后引流液突然鲜红且量增多时，护士需要怎样分辨轻重缓急，并向下一班怎样交代？", "引流", "鲜红", "下一班"),
        recommendation_case("发热体温单复测", "发热患者体温单补录时，什么时候要复测、什么时候要降温后再评估、什么时候要补护理记录？", "体温单", "复测", "护理记录"),
        recommendation_case("12床18床23床风险比较", "请比较12床、18床和23床的风险重点，并告诉我谁最急、为什么最急、下一班最该盯什么。", "最急", "下一班", "风险", context_hit=True),
        recommendation_case("病区谁能等谁不能等", "从全病区角度看，谁必须马上处理、谁 30 分钟内处理、谁可以稍后处理？请说清分类依据。", "马上处理", "30分钟内处理", "分类依据", context_hit=True),

        handover_case("12床一句话交班", "给我一句能直接交给下一班的 12 床交班提醒，要体现低血压、尿量和何时联系医生。", "下一班", "尿量", "联系医生"),
        handover_case("12床18床23床比较交班", "请比较12床、18床和23床的风险重点，并分别给一句适合交给下一班的提醒。", "12床", "18床", "23床"),
        handover_case("全病区白班交班草稿", "请生成今天这个病区的白班护理交接班草稿，重点突出病情变化、高危患者和次日仍需观察的点。", "白班", "高危", "观察"),
        document_case("23床输血护理记录草稿", "帮我生成23床输血护理记录草稿，至少体现双人核对、开始时间、15分钟观察和结束后复评。", "输血护理记录", "双人核对", "15分钟"),
        document_case("16床体温单草稿", "帮我生成16床体温单电子录入草稿，体现发热复测、降温后记录和护理记录转写。", "体温单", "复测", "护理记录"),
        document_case("12床病重护理记录草稿", "帮我生成12床病重护理记录草稿，体现生命体征、出入量、护理措施和效果评价。", "病重护理记录", "生命体征", "出入量"),
        handover_case("交接班报告顺序", "护理日夜交接班报告一般要按什么顺序写，哪些类别的患者必须重点交代？", "顺序", "病重", "高危"),
        handover_case("外出请假返区交接", "外出请假患者在交接班报告里至少要写什么？返区后还要补哪些评估？", "去向", "返区", "评估"),

        autonomous_case("病区高风险患者闭环巡查", "请把病区高风险患者的闭环巡查做成一个 AI Agent 计划：先给巡查顺序，再给每床危险信号、立即措施、文书留痕和下一班延续动作。", "巡查顺序", "危险信号", "文书", "下一班", artifacts=("document_plan",)),
        autonomous_case("护士长总览版闭环", "请把全病区今班风险、关键文书、医生沟通点和下一班重点整理成一份给护士长看的 AI Agent 总览。", "护士长", "文书", "下一班", artifacts=("document_plan",)),
        autonomous_case("责任护士执行版", "请直接输出一份责任护士执行版闭环清单：从先看哪张床、先做哪项复核、先补哪份文书，到什么时候联系医生、交班怎么说、最后如何留痕。", "责任护士", "复核", "留痕", "联系医生", artifacts=("document_plan",)),
        autonomous_case("交班后持续追踪", "请模拟交班后的持续追踪：哪些患者还需要二次复核、哪些文书需要补改、哪些异常需要再次联系医生，并把这些事项串成一个持续闭环任务列表。", "二次复核", "补改", "持续闭环", "联系医生", artifacts=("document_plan",)),
        autonomous_case("晨间巡检到文书提交闭环", "请以病区晨间巡检为起点，安排一个完整 AI Agent 工作流：先排优先级，再生成交班和文书草稿，再指出哪些内容需要护士人工确认，最后形成提交前复核清单。", "优先", "交班草稿", "文书草稿", "人工确认", "复核", artifacts=("handover_batch", "document_plan")),
        autonomous_case("夜班双护士分工", "请按夜班只有两名护士的现实条件，给出病区高危患者分工顺序、30 分钟内必须完成的任务、需要联系医生的对象和下一班延续动作。", "30分钟内处理", "联系医生", "下一班", "分工", artifacts=("document_plan",)),
        autonomous_case("全病区高风险文书补录闭环", "请按 AI Agent 的方式处理全病区当前高风险患者：先识别谁最急，再把交班、文书补录和联系医生这三条线串成一个可执行闭环。", "交班", "文书", "联系医生", "闭环", artifacts=("handover_batch", "document_plan")),
        autonomous_case("多床调度和医生沟通", "如果同一时段同时出现12床输血开始、18床低氧波动、23床感染贫血需要重点交班，请用 AI Agent 方式给出床旁调度顺序、医生沟通优先级、文书安排和下一班提醒。", "调度", "医生沟通", "文书", "下一班", artifacts=("document_plan",)),
        autonomous_case("下一班任务单", "请把12床、18床、23床整理成一份下一班任务单，要求按优先级排序，每床都写最先要看的指标、待完成护理动作、需补写的文书和何时联系医生。", "下一班任务单", "优先级", "文书", "联系医生", artifacts=("document_plan",)),
        autonomous_case("交班前总复盘", "交班前最后30分钟，请用 AI Agent 方式帮我做总复盘：哪些患者还要复核、哪些文书还缺关键字段、哪些医生沟通还没闭环、下一班最需要知道什么。", "复盘", "关键字段", "下一班", "闭环", artifacts=("document_plan",)),
        autonomous_case("中医护理闭环", "请把病区里适合加上中医护理观察的患者挑出来，并给出证候观察、饮食情志护理、何时转医生以及如何纳入交班和护理留痕。", "证候", "饮食", "情志", "留痕", artifacts=("document_plan",)),
        autonomous_case("病区文书协同分层", "请设计一个病区午后批量文书协同方案：把需要补体温单、一般护理记录、输血护理记录和交班草稿的患者先分层，再给出护士和 AI Agent 的分工边界。", "体温单", "一般护理记录", "输血护理记录", "分工", artifacts=("document_plan",)),
        autonomous_case("交班报告与护理记录联动", "请把病区交班报告和一般护理记录联动起来：哪些内容适合先由 AI Agent 生成草稿，哪些必须由护士补写客观指标，怎样避免前后不一致。", "交班报告", "护理记录", "一致", "客观指标", artifacts=("document_plan",)),
    ]
    assert len(cases) == 45, len(cases)
    return cases


if __name__ == "__main__":
    raise SystemExit(
        run_suite(
            suite_name="45条实用临床与AI Agent对话回归",
            suite_id="clinical_practical45",
            report_filename="clinical_practical_dialog_regression_45.json",
            cases=build_cases(),
        )
    )
