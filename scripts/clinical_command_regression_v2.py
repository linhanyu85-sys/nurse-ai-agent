from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


API_URL = "http://127.0.0.1:8000/api/ai/chat"
REQUESTED_BY = "clinical_regression_v2"
DEPARTMENT_ID = "dep-card-01"
ROOT_DIR = Path(__file__).resolve().parents[1]
REPORT_PATH = ROOT_DIR / "logs" / "clinical_command_regression_v2.json"

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
    min_answer_length: int = 80
    require_context_hit: bool | None = None


def _single_case(name: str, prompt: str, category: str, *keywords: str) -> Case:
    return Case(
        name=name,
        user_input=prompt,
        category=category,
        mode="single_model",
        execution_profile="single_model",
        cluster_profile=None,
        expect_workflows=("single_model_chat",),
        expected_keywords=keywords,
        forbid_generic_prompt=True,
        min_answer_length=60,
    )


def build_cases() -> list[Case]:
    common_cases = [
        _single_case(
            "低氧氧疗观察要点",
            "请不要泛泛而谈，按临床夜班实际处理顺序回答：患者低氧时氧疗护理要点、重点观察指标、何时必须马上联系医生，以及处理后的留痕顺序分别是什么？",
            "一般临床",
            "氧疗",
            "联系医生",
            "留痕",
        ),
        _single_case(
            "低血糖紧急处理",
            "请按床旁应急流程详细回答：低血糖患者先做什么、什么时候复测、什么情况下要改走静脉补糖流程，以及护理记录最少要补哪些内容？",
            "一般临床",
            "低血糖",
            "复测",
            "补糖",
        ),
        _single_case(
            "夜班跌倒预防",
            "夜班只有两名护士时，高跌倒风险患者的预防措施要按优先顺序怎么做？请写出环境、陪护、起身、如厕和留痕要点。",
            "一般临床",
            "跌倒",
            "如厕",
            "看护",
        ),
        _single_case(
            "压伤预防与升级",
            "请以压伤高风险住院患者为例，按临床护理常规回答体位、皮肤检查、减压、营养观察和需要联系医生的信号。",
            "一般临床",
            "压伤",
            "皮肤",
            "联系医生",
        ),
        _single_case(
            "导尿管护理异常",
            "导尿管留置患者如果出现尿液浑浊、尿量减少或下腹不适，护士床旁先观察什么、先处理什么、什么情况要立即联系医生？",
            "一般临床",
            "导尿管",
            "尿量",
            "联系医生",
        ),
        _single_case(
            "输液外渗处理",
            "请用临床床旁语言说明输液外渗时护士第一时间要做的动作、局部观察要点、后续上报和护理记录关键字段。",
            "一般临床",
            "外渗",
            "观察",
            "护理记录",
        ),
        _single_case(
            "引流管观察与上报",
            "请按普通外科术后场景回答：引流液颜色、量、性状和引流管通畅度要怎么观察，哪些变化必须立刻联系医生？",
            "一般临床",
            "引流",
            "颜色",
            "联系医生",
        ),
        Case(
            name="交接班书写顺序",
            user_input="我现在不是要生成草稿，而是要核对制度。请按护理日夜交接班报告正式书写顺序，完整说明出科、入科、病危病重、当日手术、病情变化、次日手术、高危患者和外出请假的先后顺序与重点。",
            category="专业规范",
            execution_profile="agent",
            expect_workflows=("handover_generate",),
            expected_keywords=("出科", "入科", "高危患者"),
            forbid_generic_prompt=True,
            min_answer_length=110,
        ),
        Case(
            name="病危护理记录频次",
            user_input="请严格按护理文书规范回答：病重病危患者护理记录里，生命体征、出入液量、病情观察和护理措施分别至少多久记录一次，哪些项目必须记录到分钟？",
            category="专业规范",
            execution_profile="document",
            expect_workflows=("document_generation",),
            expected_keywords=("4小时", "分钟", "出入液量"),
            forbid_generic_prompt=True,
            min_answer_length=100,
        ),
        Case(
            name="输血记录监测节点",
            user_input="请按输血护理记录单标准化要求说明：输血前、输注最初15分钟、输血结束后60分钟内分别要记录什么，双人核对和输血反应观察怎么留痕？",
            category="专业规范",
            execution_profile="document",
            expect_workflows=("document_generation",),
            expected_keywords=("15分钟", "60分钟", "双人核对"),
            forbid_generic_prompt=True,
            min_answer_length=100,
        ),
        Case(
            name="体温单短绌脉记录",
            user_input="请按体温单书写规范详细解释短绌脉时心率、脉搏和红色斜线区域怎么画，什么情况下要在护理记录里补充说明。",
            category="专业规范",
            execution_profile="document",
            expect_workflows=("document_generation",),
            expected_keywords=("短绌脉", "心率", "脉搏"),
            forbid_generic_prompt=True,
            min_answer_length=90,
        ),
        Case(
            name="血糖POCT填写要求",
            user_input="请严格按血糖POCT记录单规范回答：日期怎么写、餐前餐后和随机血糖怎么区分、复测时怎么留痕，哪些字段必须补齐？",
            category="专业规范",
            execution_profile="document",
            expect_workflows=("document_generation",),
            expected_keywords=("POCT", "餐前", "复测"),
            forbid_generic_prompt=True,
            min_answer_length=90,
        ),
        Case(
            name="手术清点记录原则",
            user_input="请按手术物品清点记录标准化要求，完整说明手术开始前、关闭体腔前、关闭体腔后和缝皮后分别怎么清点、怎么记录、谁签名。",
            category="专业规范",
            execution_profile="document",
            expect_workflows=("document_generation",),
            expected_keywords=("双人", "关闭体腔前", "签名"),
            forbid_generic_prompt=True,
            min_answer_length=100,
        ),
        Case(
            name="外出请假交班重点",
            user_input="病区患者外出请假时，护理交接班最少要交待哪几项？请按去向、请假时间、医生意见、告知内容和返区复评顺序说明。",
            category="专业规范",
            execution_profile="agent",
            expect_workflows=("handover_generate",),
            expected_keywords=("去向", "医生意见", "返区"),
            forbid_generic_prompt=True,
            min_answer_length=90,
        ),
        Case(
            name="护理文书构成总览",
            user_input="请按临床护理工作实际，把护士最常用的标准护理文书完整列出来，并分别说明每一种文书主要记录什么、通常在什么时候填写。",
            category="专业规范",
            execution_profile="document",
            expect_workflows=("document_generation", "voice_inquiry"),
            expected_keywords=("体温单", "输血护理记录", "交接班"),
            forbid_generic_prompt=True,
            min_answer_length=120,
        ),
        Case(
            name="中医护理通用问答",
            user_input="不要结合具体患者，先从护理AI的角度解释什么叫中医辨证施护，实际落到病区时护士最常观察哪些证候线索，哪些变化要及时转医生。",
            category="专业问答",
            execution_profile="agent",
            cluster_profile="tcm_nursing_cluster",
            expect_workflows=("voice_inquiry",),
            expected_keywords=("中医", "证候", "医生"),
            forbid_generic_prompt=True,
            min_answer_length=100,
        ),
    ]

    patient_cases = [
        Case(
            name="12床低血压少尿三层处置",
            user_input="不要泛泛而谈，直接基于12床当前收缩压88 mmHg、4小时尿量85 ml、慢性心衰急性加重这组信息，按临床护理执行顺序分三层回答：现在立刻做什么、30分钟内复核什么、继续恶化时怎样联系医生。",
            category="临床病例",
            execution_profile="observe",
            expect_workflows=("voice_inquiry", "recommendation_request"),
            expected_keywords=("12床", "30分钟", "联系医生"),
            require_context_hit=True,
            min_answer_length=140,
        ),
        Case(
            name="12床15床16床优先级排序",
            user_input="把12床、15床和16床放在同一张清单里比较，先按风险紧急程度排序，再分别说明每床此刻最该盯的两个核心指标，最后给出一句适合写进交接班的提醒。",
            category="临床病例",
            execution_profile="observe",
            expect_workflows=("recommendation_request",),
            expected_keywords=("12床", "15床", "16床"),
            require_context_hit=True,
            min_answer_length=140,
        ),
        Case(
            name="全病区前三优先患者",
            user_input="从整个病区角度出发，请把目前最需要优先处理的前三位患者排出来，同时说明排序依据、本班必须闭环的护理动作，以及哪些情况要马上联系医生。",
            category="临床病例",
            execution_profile="observe",
            expect_workflows=("recommendation_request",),
            expected_keywords=("病区", "前三", "联系医生"),
            min_answer_length=140,
        ),
        Case(
            name="12床医生上报话术",
            user_input="请把12床当前低血压和尿量偏少的情况，整理成护士给值班医生打电话时能直接照着念的上报话术，并说明电话后要补记什么。",
            category="临床病例",
            execution_profile="escalate",
            expect_workflows=("recommendation_request",),
            expected_keywords=("12床", "医生您好", "补记"),
            require_context_hit=True,
            min_answer_length=120,
        ),
        Case(
            name="18床低氧下一班观察",
            user_input="请根据18床目前低氧和呼吸频率偏快的情况，整理成下一班接班后最先观察什么、什么时候复核、达到什么阈值立即联系医生。",
            category="临床病例",
            execution_profile="observe",
            expect_workflows=("recommendation_request", "voice_inquiry"),
            expected_keywords=("18床", "复核", "联系医生"),
            require_context_hit=True,
            min_answer_length=120,
        ),
        Case(
            name="19床高血压卒中风险",
            user_input="针对19床当前收缩压和舒张压都偏高的情况，请按护理优先级说明：立刻观察什么、多久复测、与卒中风险相关的神经系统观察点有哪些，什么情况必须找医生。",
            category="临床病例",
            execution_profile="observe",
            expect_workflows=("recommendation_request", "voice_inquiry"),
            expected_keywords=("19床", "复测", "卒中"),
            require_context_hit=True,
            min_answer_length=130,
        ),
        Case(
            name="22床意识波动观察",
            user_input="请围绕22床当前GCS波动的情况，整理护士床旁观察顺序、神经系统重点、生命体征复核节点，以及出现哪些变化必须立即联系医生。",
            category="临床病例",
            execution_profile="observe",
            expect_workflows=("recommendation_request", "voice_inquiry"),
            expected_keywords=("22床", "GCS", "联系医生"),
            require_context_hit=True,
            min_answer_length=130,
        ),
        Case(
            name="16床糖足感染观察",
            user_input="请结合16床糖尿病足创面和血糖波动场景，说明护士本班最重要的创面观察点、血糖复测节奏、感染加重信号，以及下一班要继续盯什么。",
            category="临床病例",
            execution_profile="observe",
            expect_workflows=("recommendation_request", "voice_inquiry"),
            expected_keywords=("16床", "血糖", "创面"),
            require_context_hit=True,
            min_answer_length=130,
        ),
        Case(
            name="23床贫血感染交班",
            user_input="请把23床当前贫血和感染风险整理成一段交给下一班的交班重点，要求包括危险原因、下一班先看什么、什么情况要立即联系医生。",
            category="临床病例",
            execution_profile="observe",
            expect_workflows=("recommendation_request", "handover_generate"),
            expected_keywords=("23床", "下一班", "联系医生"),
            require_context_hit=True,
            min_answer_length=120,
        ),
        Case(
            name="17床术后疼痛切口风险",
            user_input="17床术后疼痛评分偏高，请按临床护理真实场景回答：本班怎么评估疼痛和切口风险，怎么复评，什么情况需要联系医生或调整交班重点。",
            category="临床病例",
            execution_profile="observe",
            expect_workflows=("recommendation_request", "voice_inquiry"),
            expected_keywords=("17床", "疼痛", "切口"),
            require_context_hit=True,
            min_answer_length=120,
        ),
        Case(
            name="20床腹痛液体丢失观察",
            user_input="请针对20床腹痛加重和液体丢失风险，整理本班监测计划：生命体征、腹痛评分、出入量和补液平衡分别怎么盯，哪些变化要及时找医生。",
            category="临床病例",
            execution_profile="observe",
            expect_workflows=("recommendation_request", "voice_inquiry"),
            expected_keywords=("20床", "出入量", "找医生"),
            require_context_hit=True,
            min_answer_length=120,
        ),
        Case(
            name="12床中西医结合护理",
            user_input="从中西医结合护理角度分析12床目前证候倾向、护理观察重点、饮食与情志护理要点，并说明哪些变化要立即联系医生。",
            category="专业问答",
            execution_profile="agent",
            cluster_profile="tcm_nursing_cluster",
            expect_workflows=("recommendation_request",),
            expected_keywords=("证候", "饮食", "联系医生"),
            require_context_hit=True,
            min_answer_length=140,
        ),
        Case(
            name="12床一般护理记录草稿",
            user_input="请根据12床当前诊断、低血压和尿量偏少的情况，直接生成一份一般护理记录单草稿，要求包含病情观察、已做护理措施、处理后效果、下一步观察重点和缺失字段提示。",
            category="文书生成",
            execution_profile="document",
            expect_workflows=("document_generation",),
            expected_keywords=("草稿", "护理记录", "缺失"),
            require_context_hit=True,
            min_answer_length=100,
        ),
        Case(
            name="12床病危护理记录草稿",
            user_input="请为12床生成病重病危患者护理记录草稿，内容要体现生命体征、出入液量、病情观察、护理措施和效果，并提示还需要护士补什么字段。",
            category="文书生成",
            execution_profile="document",
            expect_workflows=("document_generation",),
            expected_keywords=("病重", "护理记录", "补"),
            require_context_hit=True,
            min_answer_length=100,
        ),
        Case(
            name="12床体温单草稿",
            user_input="请为12床生成体温单电子录入草稿，至少体现眉栏、一般项目、生命体征栏和需要护士补录的缺失字段提示。",
            category="文书生成",
            execution_profile="document",
            expect_workflows=("document_generation",),
            expected_keywords=("体温单", "眉栏", "缺失"),
            require_context_hit=True,
            min_answer_length=90,
        ),
    ]

    agent_cases = [
        Case(
            name="12床输血护理记录草稿",
            user_input="请直接为12床生成输血护理记录单草稿，要把输血前评估、开始结束时间、15分钟和60分钟监测节点、双人核对和输血反应观察都体现出来。",
            category="文书生成",
            execution_profile="document",
            expect_workflows=("document_generation",),
            expected_keywords=("输血", "15分钟", "双人核对"),
            require_context_hit=True,
            min_answer_length=100,
        ),
        Case(
            name="全病区白班交班草稿",
            user_input="生成全病区白班护理交接班草稿，先写出科、再写入科、再写病危病重、当日手术、病情变化、高危患者和外出请假，结果要方便护士长审核。",
            category="AI Agent任务",
            execution_profile="agent",
            expect_workflows=("handover_generate",),
            expected_keywords=("交接班", "草稿", "审核"),
            min_answer_length=90,
        ),
        Case(
            name="12床19床22床比较加交班提醒",
            user_input="请把12床、19床和22床放在一起比较：先按风险高低排序，再给每位患者各写一句适合交给下一班的提醒，并补一句达到什么阈值要联系医生。",
            category="AI Agent任务",
            execution_profile="observe",
            expect_workflows=("recommendation_request",),
            expected_keywords=("12床", "19床", "22床"),
            min_answer_length=140,
        ),
        Case(
            name="病区前五个高危交班重点",
            user_input="请按风险等级整理全病区今班前五个高危交班重点，每个患者给出一句交班提醒和需要立即联系医生的阈值。",
            category="AI Agent任务",
            execution_profile="agent",
            expect_workflows=("recommendation_request",),
            expected_keywords=("前五个高危", "下一班", "联系医生"),
            min_answer_length=140,
        ),
        Case(
            name="16床19床22床持续闭环",
            user_input="请持续跟进16床、19床和22床，先比较今晨风险变化，再自动生成本班交班重点和需要补记的护理文书，再按阈值给出联系医生建议，形成闭环任务。",
            category="AI Agent任务",
            execution_profile="agent",
            expect_workflows=("autonomous_care",),
            expected_keywords=("闭环", "交班", "文书"),
            min_answer_length=110,
        ),
        Case(
            name="病区巡查顺序",
            user_input="如果这一班只剩两名护士，请从全病区角度重新排一次巡查顺序，明确谁必须先看、谁可以后看、每位患者第一眼要抓什么危险信号。",
            category="AI Agent任务",
            execution_profile="observe",
            expect_workflows=("recommendation_request",),
            expected_keywords=("巡查", "先看", "危险信号"),
            min_answer_length=130,
        ),
        Case(
            name="集中汇报医生摘要",
            user_input="请把12床、19床和22床整理成给值班医生做集中汇报的摘要，要求按风险高低排序，每位患者一句主要异常、一句已做处理、一句还需要医生决策。",
            category="AI Agent任务",
            execution_profile="escalate",
            expect_workflows=("recommendation_request",),
            expected_keywords=("值班医生", "排序", "医生决策"),
            min_answer_length=140,
        ),
        Case(
            name="12床分层处置与电话话术",
            user_input="基于12床当前病情，请给我一套完整的护士执行版答案：第一层写立即处理动作，第二层写30分钟内必须复核并记录的指标，第三层写给医生打电话时能直接照念的话术。",
            category="AI Agent任务",
            execution_profile="escalate",
            expect_workflows=("recommendation_request",),
            expected_keywords=("第一层", "30分钟", "医生您好"),
            require_context_hit=True,
            min_answer_length=150,
        ),
        Case(
            name="12床18床23床下一班任务单",
            user_input="请把12床、18床和23床整理成下一班任务清单，每床都要写先做什么、重点观察什么、什么变化立即联系医生，以及需要补记哪份文书。",
            category="AI Agent任务",
            execution_profile="agent",
            expect_workflows=("recommendation_request", "handover_generate"),
            expected_keywords=("下一班", "联系医生", "文书"),
            min_answer_length=150,
        ),
        Case(
            name="全病区谁能等谁不能等",
            user_input="请不要只说高风险患者，直接从病区角度把“必须马上处理”“30分钟内处理”“可以稍后处理”的患者分别列出来，并说出分类依据。",
            category="AI Agent任务",
            execution_profile="observe",
            expect_workflows=("recommendation_request",),
            expected_keywords=("马上处理", "30分钟内", "分类依据"),
            min_answer_length=140,
        ),
        Case(
            name="12床自动做文书和交班",
            user_input="针对12床，请直接按AI Agent模式完成一轮闭环：先提炼当前风险，再生成本班交班重点，再生成需要补录的护理文书草稿，并把需要联系医生的阈值一起带出来。",
            category="AI Agent任务",
            execution_profile="full_loop",
            expect_workflows=("autonomous_care",),
            expected_keywords=("闭环", "交班", "文书"),
            require_context_hit=True,
            min_answer_length=130,
        ),
        Case(
            name="18床19床22床闭环协同",
            user_input="请同时处理18床、19床和22床：先比较本班最危险点，再把交班重点、医生沟通阈值和文书补录任务串起来，按真正能执行的闭环顺序输出。",
            category="AI Agent任务",
            execution_profile="full_loop",
            expect_workflows=("autonomous_care",),
            expected_keywords=("闭环", "交班", "医生"),
            min_answer_length=140,
        ),
        Case(
            name="12床16床中医护理比较",
            user_input="请从中医护理角度比较12床和16床：分别提示证候线索、饮食护理、情志护理和本班最需要转医生的风险，不要泛泛讲理论。",
            category="专业问答",
            execution_profile="agent",
            cluster_profile="tcm_nursing_cluster",
            expect_workflows=("recommendation_request", "voice_inquiry"),
            expected_keywords=("证候", "饮食", "情志"),
            min_answer_length=140,
        ),
        Case(
            name="病区交班草稿按固定顺序",
            user_input="请直接生成今天这个病区的白班护理交接班草稿，必须按出科、入科、病危病重、当日手术、病情变化、次日手术、高危患者和外出请假这个顺序来组织。",
            category="AI Agent任务",
            execution_profile="agent",
            expect_workflows=("handover_generate",),
            expected_keywords=("白班", "交接班", "顺序"),
            min_answer_length=90,
        ),
        Case(
            name="交接班高风险漏项",
            user_input="从护理带教角度说，交接班最容易漏掉的高风险信息有哪些？请按病情突变、重点治疗与时效性处置、安全风险与未完成闭环事项三类来讲。",
            category="专业规范",
            execution_profile="agent",
            expect_workflows=("handover_generate",),
            expected_keywords=("高风险", "病情突变", "闭环"),
            forbid_generic_prompt=True,
            min_answer_length=110,
        ),
        Case(
            name="输血记录节点再核对",
            user_input="请再按输血护理记录标准复核一遍：最初15分钟、结束后60分钟内、双人核对、输血反应和记录到分钟这几个点最容易出错的地方分别是什么？",
            category="专业规范",
            execution_profile="document",
            expect_workflows=("document_generation",),
            expected_keywords=("15分钟", "60分钟", "分钟"),
            forbid_generic_prompt=True,
            min_answer_length=100,
        ),
        Case(
            name="POCT跨天跨月写法",
            user_input="请严格按血糖POCT书写规范回答：首页首日日期、同页其余日期、跨月跨年时的写法、随机血糖和复测的留痕最容易错在哪里。",
            category="专业规范",
            execution_profile="document",
            expect_workflows=("document_generation",),
            expected_keywords=("POCT", "跨月", "复测"),
            forbid_generic_prompt=True,
            min_answer_length=90,
        ),
        Case(
            name="体温单发热复测与降温留痕",
            user_input="请按体温单规范说明：发热患者多久复测一次、降温后体温怎么画、什么时候需要把体温变化转写到护理记录中。",
            category="专业规范",
            execution_profile="document",
            expect_workflows=("document_generation",),
            expected_keywords=("发热", "降温", "护理记录"),
            forbid_generic_prompt=True,
            min_answer_length=100,
        ),
        Case(
            name="全病区待处理文书与交班闭环",
            user_input="请按AI Agent模式处理全病区当前高风险患者：先识别谁最急，再把交班、文书补录和联系医生这三条线串成一个可执行闭环，要求结果能直接给护士长看。",
            category="AI Agent任务",
            execution_profile="full_loop",
            expect_workflows=("autonomous_care",),
            expected_keywords=("闭环", "交班", "联系医生"),
            min_answer_length=140,
        ),
    ]

    cases = common_cases + patient_cases + agent_cases
    assert len(cases) == 50, len(cases)
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
        "conversation_id": f"clinical-reg-v2-{index:02d}-{uuid.uuid4().hex[:8]}",
    }
    payload = {k: v for k, v in payload.items() if v is not None}
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(API_URL, data=data, headers={"Content-Type": "application/json"})
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            body = resp.read().decode("utf-8")
        return json.loads(body), time.perf_counter() - started, None
    except urllib.error.HTTPError as exc:
        return None, time.perf_counter() - started, f"HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001
        return None, time.perf_counter() - started, str(exc)


def merged_text(resp: dict[str, Any]) -> str:
    parts: list[str] = [str(resp.get("summary") or "")]
    parts.extend(str(item) for item in resp.get("findings") or [])
    for item in resp.get("recommendations") or []:
        if isinstance(item, dict):
            parts.append(str(item.get("title") or ""))
        else:
            parts.append(str(item))
    return "\n".join(part for part in parts if part).strip()


def context_hit(resp: dict[str, Any]) -> bool:
    if resp.get("patient_id") or resp.get("bed_no"):
        return True
    steps = resp.get("steps") or []
    for step in steps:
        if not isinstance(step, dict):
            continue
        if str(step.get("agent") or "") == "Patient Context Agent" and str(step.get("status") or "") == "done":
            return True
    return False


def check_case(case: Case, resp: dict[str, Any] | None, elapsed: float, error: str | None) -> dict[str, Any]:
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
    if error or resp is None:
        result["reasons"].append("接口调用失败")
        return result

    workflow = str(resp.get("workflow_type") or "")
    text = merged_text(resp)
    result["workflow_type"] = workflow
    result["summary_preview"] = str(resp.get("summary") or "")[:200]

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
    if case.require_context_hit is True and not context_hit(resp):
        result["reasons"].append("未命中患者上下文")
    if case.require_context_hit is False and context_hit(resp):
        result["reasons"].append("不应命中患者上下文")

    result["passed"] = not result["reasons"]
    return result


def main() -> int:
    cases = build_cases()
    results: list[dict[str, Any]] = []

    print(f"开始回归：共 {len(cases)} 条临床命令")
    for index, case in enumerate(cases, start=1):
        resp, elapsed, error = call_api(case, index)
        checked = check_case(case, resp, elapsed, error)
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
