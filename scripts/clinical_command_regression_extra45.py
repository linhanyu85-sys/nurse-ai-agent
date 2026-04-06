from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


API_URL = "http://127.0.0.1:8000/api/ai/chat"
REQUESTED_BY = "clinical_extra45"
DEPARTMENT_ID = "dep-card-01"
ROOT_DIR = Path(__file__).resolve().parents[1]
REPORT_PATH = ROOT_DIR / "logs" / "clinical_command_regression_extra45.json"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

GENERIC_MARKERS = (
    "未命中患者上下文",
    "请补充床号",
    "建议先补充床号",
    "文书草稿生成需要患者上下文",
    "请告诉我您的具体需求",
    "无法直接给出回答",
    "请提供更具体的问题",
    "本地模型暂时不可用",
)


@dataclass
class Case:
    name: str
    user_input: str
    category: str
    mode: str = "agent_cluster"
    execution_profile: str | None = "agent"
    cluster_profile: str | None = "nursing_default_cluster"
    selected_model: str | None = None
    expect_workflows: tuple[str, ...] = ()
    expected_keywords: tuple[str, ...] = ()
    forbid_keywords: tuple[str, ...] = ()
    forbid_generic_prompt: bool = True
    min_answer_length: int = 90
    require_context_hit: bool | None = None


def single_case(name: str, prompt: str, *keywords: str, min_answer_length: int = 80) -> Case:
    return Case(
        name=name,
        user_input=prompt,
        category="一般临床问题",
        mode="single_model",
        execution_profile="single_model",
        cluster_profile=None,
        expect_workflows=("single_model_chat",),
        expected_keywords=keywords,
        min_answer_length=min_answer_length,
    )


def build_cases() -> list[Case]:
    cases = [
        single_case(
            "夜班高流量氧疗观察与升级",
            "请按夜班床旁护理实际顺序回答：如果患者正在高流量吸氧，血氧在低位波动，护士应该先观察哪些指标、多久复核一次、什么信号提示可能恶化，以及到了什么程度必须立刻联系医生并完成护理留痕？",
            "血氧",
            "联系医生",
            "留痕",
            min_answer_length=120,
        ),
        single_case(
            "低血糖口服与静脉补糖切换",
            "不要泛泛而谈，请按病房抢救前的床旁流程说明：低血糖患者先做什么、口服补糖后多久复测、什么情况下要改走静脉补糖流程、护理记录至少要记哪些内容？",
            "低血糖",
            "复测",
            "补糖",
            min_answer_length=110,
        ),
        single_case(
            "夜班跌倒高风险看护",
            "夜班只有两名护士时，面对高跌倒风险患者，环境管理、起身协助、如厕陪护、管路整理和重点看护分别要怎么排优先级？请说得像真正交代给值班护士那样。",
            "跌倒",
            "如厕",
            "看护",
            min_answer_length=110,
        ),
        single_case(
            "压伤高风险患者预防升级",
            "请按临床护理常规回答：压伤高风险患者在翻身减压、受压点皮肤评估、营养观察、潮湿失禁管理和需要联系医生的升级信号上，各自最关键的执行要点是什么？",
            "压伤",
            "皮肤",
            "联系医生",
            min_answer_length=110,
        ),
        single_case(
            "导尿管阻塞与尿量减少",
            "如果留置导尿患者突然尿量减少、下腹不适、尿液混浊，护士床旁先看什么、先处理什么、何时要警惕感染或阻塞并联系医生？请按先后顺序说清楚。",
            "导尿管",
            "尿量",
            "联系医生",
            min_answer_length=100,
        ),
        single_case(
            "输液外渗处理与记录",
            "请用病房护士能直接执行的话说明：输液外渗时第一时间要做什么，局部需要观察哪些点，什么时候要马上上报医生，以及护理记录里必须写哪些留痕内容？",
            "外渗",
            "观察",
            "护理记录",
            min_answer_length=100,
        ),
        single_case(
            "术后引流异常上报",
            "外科术后患者引流液突然鲜红、量增加或者完全不引流时，护士应该怎样判断轻重缓急、先做哪些观察和保护、什么情况必须立即联系医生？",
            "引流",
            "鲜红",
            "联系医生",
            min_answer_length=100,
        ),
        single_case(
            "镇痛后复评时点",
            "请按疼痛管理规范回答：患者用了镇痛干预后，护士为什么要做疼痛复评、一般在什么时间窗复评、复评结果在体温单和护理记录里各怎么留痕？",
            "疼痛",
            "复评",
            "体温单",
            min_answer_length=100,
        ),
        single_case(
            "术后早期活动宣教",
            "术后患者准备第一次下床活动时，护士要重点评估什么，如何一步步指导患者从坐起到站立，再到离床活动，哪些异常提示应立刻停止活动并重新评估？",
            "评估",
            "坐起",
            "重新评估",
            min_answer_length=100,
        ),
        single_case(
            "呼吸机患者夜班观察",
            "请按夜班 ICU 外围护理的实际语言回答：带呼吸机患者在夜班要持续看哪些指标、报警后先查什么、什么情况下必须马上找医生，而且交班时要重点提醒下一班什么？",
            "呼吸机",
            "报警",
            "找医生",
            min_answer_length=110,
        ),
        Case(
            name="交接班正式书写顺序",
            user_input="我现在不是要生成草稿，而是要核对制度。请按护理日夜交接班报告正式书写顺序，完整说明出科、入科、病重病危、当日手术、病情变化、次日手术/特殊检查、高危患者、外出请假和其他特殊情况的先后顺序及重点。",
            category="专业规范",
            execution_profile="agent",
            expect_workflows=("handover_generate",),
            expected_keywords=("出科", "入科", "高危患者"),
            min_answer_length=120,
        ),
        Case(
            name="外出请假交班重点",
            user_input="病区患者外出请假时，护理交接班最少要交代哪些内容？请按去向、请假时间、医生意见、告知内容、返区后复评这五个栏目顺序说清楚。",
            category="专业规范",
            execution_profile="agent",
            expect_workflows=("handover_generate",),
            expected_keywords=("去向", "医生意见", "返区"),
            min_answer_length=100,
        ),
        Case(
            name="交接班最容易漏掉的高风险信息",
            user_input="请按临床交班最容易出错的角度总结：病情突变、重点治疗时效、安全风险和未闭环事项里，各有哪些最容易漏掉的高风险信息？最好能说成护士交班时的提醒句。",
            category="专业规范",
            execution_profile="agent",
            expect_workflows=("handover_generate",),
            expected_keywords=("高风险", "交班", "下一班"),
            min_answer_length=100,
        ),
        Case(
            name="输血护理记录节点核对",
            user_input="请按输血护理记录单规范回答：输血前60分钟内、开始后最初15分钟、输血结束后60分钟内分别要记录什么，双人核对和输血反应观察怎么留痕？",
            category="专业规范",
            execution_profile="document",
            expect_workflows=("document_generation",),
            expected_keywords=("15分钟", "60分钟", "双人核对"),
            min_answer_length=100,
        ),
        Case(
            name="血糖POCT填写要求",
            user_input="请严格按血糖 POCT 记录要求回答：日期怎么写、餐前餐后和随机血糖怎么区分、复测时怎么补录、哪些栏位必须补全？",
            category="专业规范",
            execution_profile="document",
            expect_workflows=("document_generation",),
            expected_keywords=("POCT", "餐前", "复测"),
            min_answer_length=90,
        ),
        Case(
            name="体温单发热复测与降温留痕",
            user_input="请按体温单标准格式解释：患者发热后多久复测，降温后的体温为什么要用红圈和虚线，什么情况下需要把经过转写到护理记录里？",
            category="专业规范",
            execution_profile="document",
            expect_workflows=("document_generation",),
            expected_keywords=("发热", "降温", "虚线"),
            min_answer_length=90,
        ),
        Case(
            name="手术物品清点记录原则",
            user_input="请按手术物品清点记录的标准化要求，说明手术开始前、关闭体腔前、关闭体腔后、缝合皮肤后分别怎么清点、怎么即时记录、谁签名，遇到数量不符怎么处理。",
            category="专业规范",
            execution_profile="document",
            expect_workflows=("document_generation",),
            expected_keywords=("双人", "关闭体腔前", "签名"),
            min_answer_length=110,
        ),
        Case(
            name="危重护理记录频次与分钟级要求",
            user_input="请严格按危重护理记录书写要求回答：生命体征、出入液量、病情观察和护理措施分别至少多久记一次，哪些项目必须精确到分钟，病情变化时如何补记？",
            category="专业规范",
            execution_profile="document",
            expect_workflows=("document_generation",),
            expected_keywords=("4小时", "分钟", "出入液量"),
            min_answer_length=100,
        ),
        Case(
            name="体温单短绌脉绘图规则",
            user_input="请按体温单书写规范详细解释：短绌脉时心率、脉搏、红圈、红点和红色斜线之间分别是什么关系，为什么必须双人同步测量，什么情况要同步写入护理记录。",
            category="专业规范",
            execution_profile="document",
            expect_workflows=("document_generation",),
            expected_keywords=("短绌脉", "心率", "脉搏"),
            min_answer_length=100,
        ),
        Case(
            name="护理文书构成总览",
            user_input="请站在病区护士的角度，完整列出最常用的标准护理文书，并说明每一类文书主要记录什么、通常在什么时点填写、适合由 AI 先生成半结构化草稿的字段有哪些。",
            category="专业规范",
            execution_profile="document",
            expect_workflows=("document_generation", "voice_inquiry"),
            expected_keywords=("体温单", "输血护理记录", "交接班"),
            min_answer_length=120,
        ),
        Case(
            name="12床低血压少尿三层处置",
            user_input="不要泛泛而谈，直接基于12床当前收缩压88 mmHg、4小时尿量偏少、慢性心衰急性加重这组信息，按临床护理执行顺序分三层回答：现在立刻做什么、30分钟内复核什么、继续恶化时怎样联系医生。",
            category="病例处置",
            execution_profile="observe",
            expect_workflows=("recommendation_request",),
            expected_keywords=("12床", "30分钟", "联系医生"),
            require_context_hit=True,
            min_answer_length=130,
        ),
        Case(
            name="18床低氧下一班交班重点",
            user_input="请根据18床当前低氧和呼吸频率偏快的情况，整理成下一班接手后最先看什么、多久复核一次、什么阈值必须立即联系医生的交班话术。",
            category="病例处置",
            execution_profile="handover",
            expect_workflows=("handover_generate", "recommendation_request"),
            expected_keywords=("18床", "下一班", "联系医生"),
            require_context_hit=True,
            min_answer_length=120,
        ),
        Case(
            name="23床贫血感染交班与上报",
            user_input="请把23床当前贫血合并感染风险整理成一段临床可直接用的交接班重点，同时补上一句什么时候必须联系医生、联系时重点汇报哪三个客观指标。",
            category="病例处置",
            execution_profile="handover",
            expect_workflows=("handover_generate", "recommendation_request"),
            expected_keywords=("23床", "联系医生", "指标"),
            require_context_hit=True,
            min_answer_length=120,
        ),
        Case(
            name="20床腹痛液体丢失观察",
            user_input="请结合20床腹痛、液体丢失风险和进食受限的背景，按床旁观察顺序告诉我护士要先盯哪些生命体征和症状、出入量怎么记、什么情况要马上找医生。",
            category="病例处置",
            execution_profile="observe",
            expect_workflows=("recommendation_request",),
            expected_keywords=("20床", "出入量", "找医生"),
            require_context_hit=True,
            min_answer_length=120,
        ),
        Case(
            name="12床中西医结合护理分析",
            user_input="请从中西医结合护理角度分析12床：既要说西医护理观察重点，也要补上可能的证候线索、饮食调护、情志护理和什么时候必须联系医生。",
            category="病例处置",
            execution_profile="observe",
            cluster_profile="tcm_nursing_cluster",
            expect_workflows=("recommendation_request",),
            expected_keywords=("证候", "饮食", "情志"),
            require_context_hit=True,
            min_answer_length=140,
        ),
        Case(
            name="12床输血护理记录草稿",
            user_input="请直接为12床生成一份符合输血护理记录单格式的半结构化草稿，要求突出双人核对、开始时间、15分钟观察、结束后60分钟复评和输血反应留痕。",
            category="病例处置",
            execution_profile="document",
            expect_workflows=("document_generation",),
            expected_keywords=("双人核对", "15分钟", "60分钟"),
            require_context_hit=True,
            min_answer_length=110,
        ),
        Case(
            name="12床体温单补录草稿",
            user_input="请给12床生成体温单电子录入草稿，重点体现日期、住院天数、生命体征时间点、发热复测、降温后红圈和虚线标记，以及异常需要转写到护理记录的地方。",
            category="病例处置",
            execution_profile="document",
            expect_workflows=("document_generation",),
            expected_keywords=("体温单", "发热", "护理记录"),
            require_context_hit=True,
            min_answer_length=110,
        ),
        Case(
            name="12床一般护理记录草稿",
            user_input="请直接帮我生成12床一般护理记录半结构化草稿，要求包含当前病情观察、已执行护理措施、效果评价、下一班观察要点，并保留我后续手动修改的空间。",
            category="病例处置",
            execution_profile="document",
            expect_workflows=("document_generation",),
            expected_keywords=("护理记录", "观察", "下一班"),
            require_context_hit=True,
            min_answer_length=110,
        ),
        Case(
            name="12床15床16床优先级排序",
            user_input="把12床、15床和16床放在同一张清单里比较，先按紧急程度排序，再分别说明每床此刻最该盯的两个核心指标，最后给一句适合写进交接班的话。",
            category="病例处置",
            execution_profile="observe",
            expect_workflows=("recommendation_request", "handover_generate"),
            expected_keywords=("12床", "15床", "16床"),
            require_context_hit=True,
            min_answer_length=140,
        ),
        Case(
            name="12床18床23床比较交班提醒",
            user_input="请比较12床、18床和23床的风险重点，并分别给一句适合交给下一班的提醒，要求先说谁最急、为什么急，再给每床一句实用交班语句。",
            category="病例处置",
            execution_profile="handover",
            expect_workflows=("handover_generate", "recommendation_request"),
            expected_keywords=("12床", "18床", "23床"),
            require_context_hit=True,
            min_answer_length=140,
        ),
        Case(
            name="全病区前三优先患者",
            user_input="从整个病区出发，把当前最需要优先处理的前三位患者排出来，同时说明排序依据、本班必须闭环的护理动作，以及哪些情况需要马上联系医生。",
            category="病例处置",
            execution_profile="observe",
            expect_workflows=("recommendation_request", "autonomous_care"),
            expected_keywords=("病区", "前三", "联系医生"),
            min_answer_length=130,
        ),
        Case(
            name="全病区谁能等谁不能等",
            user_input="请把当前全病区患者按“必须马上处理、30分钟内处理、可以稍后处理”三层分类，并写明分类依据，回答要能直接给护士长做排班与巡查决策。",
            category="病例处置",
            execution_profile="observe",
            expect_workflows=("recommendation_request",),
            expected_keywords=("马上处理", "30分钟内处理", "分类依据"),
            min_answer_length=130,
        ),
        Case(
            name="病区少人力巡查顺序",
            user_input="如果本班只剩两名护士，请按病区风险给出真正可执行的巡查顺序：谁必须先看、谁可以稍后看、每一轮巡查看到什么危险信号时要立即升级处理。",
            category="病例处置",
            execution_profile="observe",
            expect_workflows=("recommendation_request",),
            expected_keywords=("巡查", "先看", "危险信号"),
            min_answer_length=130,
        ),
        Case(
            name="集中汇报值班医生摘要",
            user_input="请把全病区最需要值班医生马上知道的情况整理成一份集中汇报摘要，要求按风险高低排序，每个床位都写一句最关键的客观指标和需要医生决策的点。",
            category="病例处置",
            execution_profile="escalate",
            expect_workflows=("recommendation_request",),
            expected_keywords=("值班医生", "排序", "客观指标"),
            min_answer_length=130,
        ),
        Case(
            name="前五个高危交班重点",
            user_input="请按风险等级整理全病区今班前五个高危交班重点，要求每个床位都写清危险原因、下一班最该盯的指标和达到什么阈值立即联系医生。",
            category="病例处置",
            execution_profile="observe",
            expect_workflows=("recommendation_request",),
            expected_keywords=("前五", "下一班", "联系医生"),
            min_answer_length=140,
        ),
        Case(
            name="白班固定顺序病区交班草稿",
            user_input="现在请按白班正式交接班格式，生成全病区交接班草稿，内容必须按出科、入科、病重病危、当日手术、病情变化、次日手术/特殊检查、高危患者、外出请假和其他特殊情况的固定顺序组织。",
            category="AI Agent任务",
            execution_profile="handover",
            expect_workflows=("handover_generate",),
            expected_keywords=("白班", "顺序", "交接班"),
            min_answer_length=110,
        ),
        Case(
            name="全病区高风险文书补录闭环",
            user_input="请按 AI Agent 的方式处理全病区当前高风险患者：先识别谁最急，再把交班、文书补录和联系医生这三条线串成一个可执行闭环，结果要能直接给责任护士照着做。",
            category="AI Agent任务",
            execution_profile="full_loop",
            expect_workflows=("autonomous_care",),
            expected_keywords=("闭环", "交班", "联系医生"),
            min_answer_length=150,
        ),
        Case(
            name="今班异常到下一班任务闭环",
            user_input="请把病区今班已经发现的异常情况，整理成一份面向下一班的任务闭环清单：每项都写明谁负责、先后顺序、需要复核的指标，以及何时必须升级报告。",
            category="AI Agent任务",
            execution_profile="full_loop",
            expect_workflows=("autonomous_care",),
            expected_keywords=("下一班", "复核", "升级"),
            min_answer_length=140,
        ),
        Case(
            name="12床单床闭环计划",
            user_input="请围绕12床生成一个单床 AI Agent 闭环计划：包括本班观察要点、需要补的护理文书、需要向医生反馈的点、交给下一班的提醒，以及完成后如何留痕。",
            category="AI Agent任务",
            execution_profile="full_loop",
            expect_workflows=("autonomous_care",),
            expected_keywords=("12床", "护理文书", "留痕"),
            require_context_hit=True,
            min_answer_length=140,
        ),
        Case(
            name="12床18床23床下一班任务单",
            user_input="请把12床、18床、23床整理成一份下一班任务单，要求按优先级排序，每床都包含最先要看的指标、待完成护理动作、需要补写的文书和何时联系医生。",
            category="AI Agent任务",
            execution_profile="full_loop",
            expect_workflows=("autonomous_care", "recommendation_request"),
            expected_keywords=("12床", "18床", "23床"),
            min_answer_length=150,
        ),
        Case(
            name="晨间巡检到文书提交闭环",
            user_input="请以病区晨间巡检为起点，安排一个完整 AI Agent 工作流：先排优先级，再生成交班和文书草稿，再指出哪些内容需要护士人工确认，最后形成提交前复核清单。",
            category="AI Agent任务",
            execution_profile="full_loop",
            expect_workflows=("autonomous_care",),
            expected_keywords=("优先级", "文书草稿", "复核"),
            min_answer_length=150,
        ),
        Case(
            name="多床风险比对与医生汇总",
            user_input="请把12床低血压、18床低氧、23床感染贫血这三类问题汇总成 AI Agent 的值班医生汇报方案，要求先排序，再给每床一句汇报话术，并标出哪一项必须先电话上报。",
            category="AI Agent任务",
            execution_profile="full_loop",
            expect_workflows=("autonomous_care", "recommendation_request"),
            expected_keywords=("值班医生", "排序", "电话"),
            min_answer_length=150,
        ),
        Case(
            name="病区午后批量文书协同",
            user_input="请设计一个病区午后批量文书协同方案：把需要补体温单、一般护理记录、输血护理记录和交接班草稿的患者先分层，再给出护士和 AI Agent 的分工边界。",
            category="AI Agent任务",
            execution_profile="full_loop",
            expect_workflows=("autonomous_care",),
            expected_keywords=("体温单", "输血护理记录", "分工"),
            min_answer_length=150,
        ),
        Case(
            name="中医特色病区协同流程",
            user_input="请以中医特色护理病区为背景，设计一个 AI Agent 协同流程：既要完成常规风险排序和交接班，也要补上证候观察、饮食调护、情志护理和转医生时机。",
            category="AI Agent任务",
            execution_profile="full_loop",
            cluster_profile="tcm_nursing_cluster",
            expect_workflows=("autonomous_care",),
            expected_keywords=("证候", "饮食", "情志"),
            min_answer_length=160,
        ),
        Case(
            name="病区高风险患者闭环巡查",
            user_input="请把病区高风险患者的闭环巡查做成一个 AI Agent 计划：先给巡查顺序，再给每床危险信号、立即措施、文书留痕和下一班延续动作，结果要实用到值班护士能直接照着执行。",
            category="AI Agent任务",
            execution_profile="full_loop",
            expect_workflows=("autonomous_care",),
            expected_keywords=("巡查顺序", "危险信号", "留痕"),
            min_answer_length=160,
        ),
        Case(
            name="护士长总览版闭环摘要",
            user_input="请把全病区今班风险、关键文书、医生沟通点和下一班重点整理成一份给护士长看的 AI Agent 总览，要求突出谁最急、哪些事项已经闭环、哪些还需要人工确认。",
            category="AI Agent任务",
            execution_profile="full_loop",
            expect_workflows=("autonomous_care",),
            expected_keywords=("护士长", "闭环", "人工确认"),
            min_answer_length=150,
        ),
        Case(
            name="病区交班前总复盘",
            user_input="交班前最后30分钟，请用 AI Agent 方式帮我做总复盘：哪些患者还要复核、哪些文书还缺关键字段、哪些医生沟通还没闭环、下一班最需要知道什么。",
            category="AI Agent任务",
            execution_profile="full_loop",
            expect_workflows=("autonomous_care",),
            expected_keywords=("复核", "关键字段", "下一班"),
            min_answer_length=150,
        ),
        Case(
            name="夜班前风险与文书双清单",
            user_input="请给夜班准备一份双清单：一份是风险优先级清单，一份是文书与交班待办清单，要求按病区真实工作流排序，能直接指导夜班护士怎么分工。",
            category="AI Agent任务",
            execution_profile="full_loop",
            expect_workflows=("autonomous_care",),
            expected_keywords=("夜班", "分工", "待办"),
            min_answer_length=150,
        ),
        Case(
            name="交班后追踪闭环任务",
            user_input="请模拟交班后的持续追踪：哪些患者还需要二次复核、哪些文书需要补改、哪些异常需要再次联系医生，并把这些事项串成一个持续闭环任务列表。",
            category="AI Agent任务",
            execution_profile="full_loop",
            expect_workflows=("autonomous_care",),
            expected_keywords=("二次复核", "补改", "持续闭环"),
            min_answer_length=150,
        ),
        Case(
            name="多床输血与低氧混合场景调度",
            user_input="如果同一时段同时出现12床输血开始、18床低氧波动、23床感染贫血需要重点交班，请用 AI Agent 方式给出床旁调度顺序、医生沟通优先级、文书安排和下一班提醒。",
            category="AI Agent任务",
            execution_profile="full_loop",
            expect_workflows=("autonomous_care",),
            expected_keywords=("调度", "医生沟通", "下一班"),
            min_answer_length=160,
        ),
        Case(
            name="交班报告与护理记录联动",
            user_input="请把病区交班报告和一般护理记录联动起来：哪些内容适合先由 AI Agent 生成草稿，哪些必须由护士补写客观指标，怎样避免交班和护理记录内容前后不一致。",
            category="AI Agent任务",
            execution_profile="full_loop",
            expect_workflows=("autonomous_care",),
            expected_keywords=("交班报告", "护理记录", "一致"),
            min_answer_length=150,
        ),
        Case(
            name="责任护士闭环执行版",
            user_input="请直接输出一份责任护士执行版闭环清单：从先看哪张床、先做哪项复核、先补哪份文书，到什么时候联系医生、交班怎么说、最后如何留痕，全部用临床可执行语言写出来。",
            category="AI Agent任务",
            execution_profile="full_loop",
            expect_workflows=("autonomous_care",),
            expected_keywords=("责任护士", "复核", "留痕"),
            min_answer_length=170,
        ),
    ]

    assert len(cases) == 52, len(cases)
    return cases


def call_api(case: Case, index: int) -> tuple[dict[str, Any] | None, float, str | None]:
    payload = {
        "user_input": case.user_input,
        "mode": case.mode,
        "execution_profile": case.execution_profile,
        "cluster_profile": case.cluster_profile,
        "selected_model": case.selected_model,
        "department_id": DEPARTMENT_ID,
        "requested_by": f"{REQUESTED_BY}_{index:02d}",
        "conversation_id": f"clinical-extra45-{index:02d}-{uuid.uuid4().hex[:8]}",
    }
    payload = {key: value for key, value in payload.items() if value is not None}
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(API_URL, data=data, headers={"Content-Type": "application/json"})
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            body = response.read().decode("utf-8")
        return json.loads(body), time.perf_counter() - started, None
    except urllib.error.HTTPError as exc:
        return None, time.perf_counter() - started, f"HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001
        return None, time.perf_counter() - started, str(exc)


def merged_text(response: dict[str, Any]) -> str:
    parts: list[str] = [str(response.get("summary") or "")]
    parts.extend(str(item) for item in response.get("findings") or [])
    for item in response.get("recommendations") or []:
        if isinstance(item, dict):
            parts.append(str(item.get("title") or ""))
        else:
            parts.append(str(item))
    return "\n".join(part for part in parts if part).strip()


def context_hit(response: dict[str, Any]) -> bool:
    if response.get("patient_id") or response.get("bed_no"):
        return True
    for step in response.get("steps") or []:
        if not isinstance(step, dict):
            continue
        if str(step.get("agent") or "") == "Patient Context Agent" and str(step.get("status") or "") == "done":
            return True
    return False


def check_case(case: Case, response: dict[str, Any] | None, elapsed: float, error: str | None) -> dict[str, Any]:
    result = {
        "name": case.name,
        "category": case.category,
        "elapsed_sec": round(elapsed, 3),
        "passed": False,
        "error": error,
        "reasons": [],
        "workflow_type": None,
        "summary_preview": None,
    }
    if error or response is None:
        result["reasons"].append("接口调用失败")
        return result

    workflow = str(response.get("workflow_type") or "")
    text = merged_text(response)
    result["workflow_type"] = workflow
    result["summary_preview"] = str(response.get("summary") or "")[:240]

    if case.expect_workflows and workflow not in case.expect_workflows:
        result["reasons"].append(f"工作流不匹配：{workflow}")
    if len(text) < case.min_answer_length:
        result["reasons"].append("回答过短")
    if case.forbid_generic_prompt and any(marker in text for marker in GENERIC_MARKERS):
        result["reasons"].append("出现泛化追问或要求补床号")
    for keyword in case.expected_keywords:
        if keyword not in text:
            result["reasons"].append(f"缺少关键词：{keyword}")
    for keyword in case.forbid_keywords:
        if keyword in text:
            result["reasons"].append(f"命中禁用关键词：{keyword}")
    if case.require_context_hit is True and not context_hit(response):
        result["reasons"].append("未命中患者上下文")
    if case.require_context_hit is False and context_hit(response):
        result["reasons"].append("不应命中患者上下文")

    result["passed"] = not result["reasons"]
    return result


def main() -> int:
    cases = build_cases()
    results: list[dict[str, Any]] = []

    print(f"开始回归：共 {len(cases)} 条新增复杂临床对话")
    for index, case in enumerate(cases, start=1):
        response, elapsed, error = call_api(case, index)
        checked = check_case(case, response, elapsed, error)
        results.append(checked)
        status = "PASS" if checked["passed"] else "FAIL"
        print(f"[{status}] {index:02d}. {case.category} / {case.name} ({checked['elapsed_sec']}s)")
        if checked["reasons"]:
            for reason in checked["reasons"]:
                print(f"  - {reason}")

    passed = sum(1 for item in results if item["passed"])
    failed = len(results) - passed
    by_category: dict[str, dict[str, int]] = {}
    for item in results:
        bucket = by_category.setdefault(item["category"], {"passed": 0, "failed": 0})
        bucket["passed" if item["passed"] else "failed"] += 1

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "api_url": API_URL,
        "total": len(results),
        "passed": passed,
        "failed": failed,
        "by_category": by_category,
        "results": results,
    }
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("")
    print(json.dumps({"total": len(results), "passed": passed, "failed": failed, "report": str(REPORT_PATH)}, ensure_ascii=False))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
