from __future__ import annotations

import json
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


API_URL = "http://127.0.0.1:8000/api/ai/chat"
REQUESTED_BY = "u_nurse_01"
DEPARTMENT_ID = "dep-card-01"
ROOT_DIR = Path(__file__).resolve().parents[1]
REPORT_PATH = ROOT_DIR / "logs" / "clinical_command_regression.json"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

GENERIC_MARKERS = (
    "未命中具体患者上下文",
    "请补充床号",
    "补充床号",
    "建议先补充床号",
    "文书草稿生成需要患者上下文",
)

WORKFLOW_ALIASES = {
    "recommendation": "recommendation_request",
    "single_model": "single_model_chat",
}


@dataclass
class Case:
    name: str
    user_input: str
    mode: str = "agent_cluster"
    cluster_profile: str = "nursing_default_cluster"
    execution_profile: str | None = None
    selected_model: str | None = None
    expect_bed: str | None = None
    expect_workflows: tuple[str, ...] = ()
    expected_keywords: tuple[str, ...] = ()
    forbid_keywords: tuple[str, ...] = ()
    forbid_generic_prompt: bool = False
    forbid_bed_requirement: bool = False
    require_context_hit: bool | None = None
    min_answer_length: int = 80


def build_cases() -> list[Case]:
    return [
        Case(
            name="12床低血压少尿分层处置",
            user_input=(
                "不要泛泛而谈，直接基于12床当前收缩压88 mmHg、4小时尿量85 ml、慢性心衰急性加重这组信息，"
                "按临床护理实际执行顺序分三层回答：第一层写我现在立刻该做的护理动作；"
                "第二层写30分钟内必须复核并记录的指标；第三层写如果继续恶化应该怎么向医生上报。"
                "每一层都尽量写成护士交班或电话上报时能直接使用的话。"
            ),
            execution_profile="observe",
            expect_bed="12",
            expect_workflows=("voice_inquiry", "recommendation"),
            expected_keywords=("12床", "30分钟", "上报"),
            forbid_generic_prompt=True,
            require_context_hit=True,
            min_answer_length=120,
        ),
        Case(
            name="12床15床16床多床优先级排序",
            user_input=(
                "把12床、15床和16床放在同一张清单里比较，先按风险紧急程度排序，"
                "再分别说明每床此刻最该盯的2到3个核心指标，并给出一句适合写进交接班报告的提醒。"
                "不要只说笼统高风险，要把排序理由写清楚。"
            ),
            execution_profile="observe",
            expect_workflows=("voice_inquiry", "recommendation"),
            expected_keywords=("12床", "15床", "16床", "排序"),
            require_context_hit=True,
            min_answer_length=140,
        ),
        Case(
            name="全病区前三优先与闭环建议",
            user_input=(
                "从整个病区的角度，不要只列风险标签。请把目前最需要优先处理的前三位患者排出来，"
                "同时说明排序依据、每位患者本班必须闭环的护理动作，以及哪些情况需要马上联系医生。"
                "最后再给我一段适合护士长快速浏览的总括结论。"
            ),
            execution_profile="observe",
            expect_workflows=("voice_inquiry", "recommendation", "autonomous_care"),
            expected_keywords=("前三", "病区", "联系医生"),
            min_answer_length=140,
        ),
        Case(
            name="病重病危护理记录规范问答",
            user_input=(
                "我现在不是要看某一个患者，而是要核对制度。请按护理文书书写基本规范，详细说明病重病危患者护理记录里"
                "生命体征、出入液量、病情观察和护理措施分别至少多久记录一次，哪些内容需要记录到分钟，"
                "哪些内容要按班次或24小时总结，要求能直接用于带教。"
            ),
            execution_profile="document",
            expect_workflows=("document_generation",),
            expected_keywords=("每4小时", "分钟", "24小时"),
            forbid_bed_requirement=True,
            min_answer_length=120,
        ),
        Case(
            name="护理交接班书写顺序规范",
            user_input=(
                "先不要生成草稿，而是按护理日夜交接班报告的正式书写顺序，完整讲清楚白班和夜班交接时先写什么后写什么，"
                "尤其是出科、入科、病重病危、当日手术、病情变化、次日手术和高危患者这些部分各自的重点。"
            ),
            execution_profile="observe",
            expect_workflows=("handover_generate", "document_generation", "voice_inquiry"),
            expected_keywords=("出科", "入科", "高危患者"),
            forbid_bed_requirement=True,
            min_answer_length=120,
        ),
        Case(
            name="12床一般护理记录草稿",
            user_input=(
                "请根据12床当前诊断、低血压和尿量偏少的情况，直接生成一份一般护理记录单草稿。"
                "要求按标准护理文书格式写，内容要包含病情观察、已经采取的护理措施、处理后效果、"
                "下一步观察重点和护士需要补录的缺失字段提示。"
            ),
            execution_profile="document",
            expect_bed="12",
            expect_workflows=("document_generation",),
            expected_keywords=("草稿", "护理记录", "补录"),
            forbid_generic_prompt=True,
            require_context_hit=True,
        ),
        Case(
            name="12床输血护理记录草稿",
            user_input=(
                "请为12床生成一份输血护理记录单草稿。要求体现输血前评估、双人核对、"
                "输血开始与结束时间栏位、输注过程中生命体征监测节点、输血反应观察，"
                "以及目前还需要护士人工补录的字段。"
            ),
            execution_profile="document",
            expect_bed="12",
            expect_workflows=("document_generation",),
            expected_keywords=("输血", "生命体征", "补录"),
            forbid_generic_prompt=True,
            require_context_hit=True,
        ),
        Case(
            name="16床血糖记录草稿",
            user_input=(
                "结合16床当前血糖波动和感染扩散风险，为我生成一份血糖测量记录单草稿，"
                "并在草稿后面单独列出还需要人工补录的时间点、餐前餐后栏目、随机血糖或血酮体等可扩展字段。"
            ),
            execution_profile="document",
            expect_bed="16",
            expect_workflows=("document_generation",),
            expected_keywords=("血糖", "时间点", "补录"),
            forbid_generic_prompt=True,
            require_context_hit=True,
        ),
        Case(
            name="全病区白班交接班草稿",
            user_input=(
                "请直接生成今天这个病区的白班护理交接班草稿，要求严格按正式书写顺序组织："
                "先出科，再入科，再病重病危，当日手术，病情变化，次日手术和特殊检查，高危患者，外出请假以及其他特殊情况。"
                "重点把16床、19床、22床、23床写清楚，每床都要有本班重点和下一班提醒。"
            ),
            execution_profile="document",
            expect_workflows=("handover_generate",),
            expected_keywords=("病区", "交接班", "16床"),
            min_answer_length=110,
        ),
        Case(
            name="12床16床19床一句话交班提醒",
            user_input=(
                "我要做快速口头交班，请把12床、16床和19床各自最关键的风险和下一班最该盯的点压缩成一句话提醒，"
                "同时再补一段总括，告诉接班护士这三床里面谁最容易在短时间内出问题、为什么。"
            ),
            execution_profile="escalate",
            expect_workflows=("handover_generate", "recommendation"),
            expected_keywords=("12床", "16床", "19床"),
            require_context_hit=True,
            min_answer_length=120,
        ),
        Case(
            name="19床医生上报话术",
            user_input=(
                "请按真实临床沟通口径，给我写一段用于联系值班医生的上报话术：对象是19床，当前收缩压176 mmHg、"
                "舒张压104 mmHg，并且系统提示卒中风险和病情波动风险。话术里要包括我已观察到的重点、"
                "已经做了什么、还需要医生明确哪些医嘱，以及如果医生追问我还要补哪些数据。"
            ),
            execution_profile="escalate",
            expect_bed="19",
            expect_workflows=("recommendation", "voice_inquiry"),
            expected_keywords=("医生", "上报", "医嘱"),
            forbid_generic_prompt=True,
            require_context_hit=True,
            min_answer_length=120,
        ),
        Case(
            name="22床意识波动观察与上报",
            user_input=(
                "围绕22床当前GCS 12分波动、收缩压152 mmHg、再出血风险和意识波动风险，"
                "给护士写一份观察与上报提纲，分成先观察什么、先记录什么、什么变化要立刻打电话、"
                "打电话时怎么一句话说明重点四部分。"
            ),
            execution_profile="escalate",
            expect_bed="22",
            expect_workflows=("recommendation", "voice_inquiry"),
            expected_keywords=("GCS", "上报", "记录"),
            forbid_generic_prompt=True,
            require_context_hit=True,
            min_answer_length=120,
        ),
        Case(
            name="中医通用护理思路",
            user_input=(
                "现在不是问具体患者，我要的是中医护理通用思路。请针对心衰合并水肿、气短乏力这一类患者，"
                "从中医护理角度详细讲讲可能的证候线索、当班最需要观察的舌苔寒热和水湿表现、"
                "日常护理与饮食起居提醒，以及哪些表现一旦出现必须立刻转医生处理。"
            ),
            cluster_profile="tcm_nursing_cluster",
            execution_profile="observe",
            expect_workflows=("voice_inquiry",),
            expected_keywords=("证候", "护理观察", "转医生"),
            forbid_bed_requirement=True,
            min_answer_length=120,
        ),
        Case(
            name="12床中西医结合护理重点",
            user_input=(
                "针对12床目前低血压、尿量偏少、慢性心衰急性加重这个状态，给我一份中西医结合的护理观察重点。"
                "前半部分按中医证候线索写，后半部分按现代护理监测写，最后单独列出哪些变化出现时必须立刻联系医生。"
            ),
            cluster_profile="tcm_nursing_cluster",
            execution_profile="observe",
            expect_bed="12",
            expect_workflows=("voice_inquiry", "recommendation"),
            expected_keywords=("证候", "尿量", "联系医生"),
            forbid_generic_prompt=True,
            require_context_hit=True,
            min_answer_length=120,
        ),
        Case(
            name="16床持续跟进任务",
            user_input=(
                "请为16床启动一轮持续跟进任务，不只是给一句建议。我要你围绕血糖波动、感染扩散风险和生命体征复核，"
                "先说明你打算连续盯哪些信号、每一步准备做什么、哪些动作需要人工批准、最后准备产出什么交班或文书结果。"
            ),
            execution_profile="full_loop",
            expect_bed="16",
            expect_workflows=("autonomous_care",),
            expected_keywords=("持续", "人工批准", "文书"),
            forbid_generic_prompt=True,
            require_context_hit=True,
            min_answer_length=120,
        ),
        Case(
            name="19床22床联合持续跟进",
            user_input=(
                "把19床和22床作为本班重点患者，设计一轮持续跟进流程：先明确哪一床优先级更高，"
                "再给出各自的观察节奏、医生沟通触发条件、需要留痕的文书节点和下一班接手前必须闭环的事项。"
            ),
            execution_profile="full_loop",
            expect_workflows=("autonomous_care",),
            expected_keywords=("19床", "22床", "闭环"),
            require_context_hit=True,
            min_answer_length=130,
        ),
        Case(
            name="文书模板与导入能力问答",
            user_input=(
                "我现在不想看某个患者，而是想确认系统文书能力。请详细告诉我目前护理文书模块支持哪些标准模板，"
                "导入txt或docx模板后会怎么转换成半结构化字段，护士进去以后可以改哪些部分，审核和提交又怎么走。"
            ),
            execution_profile="document",
            expect_workflows=("document_generation",),
            expected_keywords=("txt", "docx", "半结构化"),
            forbid_bed_requirement=True,
            min_answer_length=120,
        ),
        Case(
            name="12床输血记录缺失字段梳理",
            user_input=(
                "不要直接再生成一份新的草稿，我想让你先站在文书质控角度看12床当前输血护理记录草稿。"
                "请把已经有的字段、明显缺失但必须补录的字段、可以稍后补录的字段，"
                "以及最容易漏写的风险提示分别列出来，最后告诉我现在立刻补录该按什么先后顺序最稳妥。"
            ),
            execution_profile="document",
            expect_bed="12",
            expect_workflows=("document_generation",),
            expected_keywords=("缺失", "补录", "输血"),
            forbid_generic_prompt=True,
            require_context_hit=True,
            min_answer_length=120,
        ),
        Case(
            name="12床15床16床时间点监测表",
            user_input=(
                "把12床、15床和16床今天这一班最需要盯的监测内容整理成一个护士能直接执行的时间点清单，"
                "格式用时间点-床号-要看什么-什么结果要升级处理。每床至少列2到3个关键监测点。"
            ),
            execution_profile="observe",
            expect_workflows=("voice_inquiry", "recommendation"),
            expected_keywords=("时间点", "12床", "16床"),
            require_context_hit=True,
            min_answer_length=120,
        ),
        Case(
            name="少人力情况下病区排序",
            user_input=(
                "假设这一班突然只剩两名护士，但病区里16床、19床、22床、23床和18床都各有风险，"
                "请按临床现实给出优先巡查和优先处理顺序，并解释为什么这样排。"
            ),
            execution_profile="observe",
            expect_workflows=("voice_inquiry", "recommendation"),
            expected_keywords=("优先", "16床", "22床"),
            min_answer_length=120,
        ),
        Case(
            name="23床交接与贫血观察",
            user_input=(
                "为23床准备一段适合写入护理交接班报告的重点内容，围绕贫血风险、感染风险、恶露观察、"
                "体温和心率趋势来写，先写本班已经观察到的重点，再写下一班必须盯的内容，最后加一句升级汇报条件。"
            ),
            execution_profile="document",
            expect_bed="23",
            expect_workflows=("handover_generate", "document_generation"),
            expected_keywords=("23床", "下一班", "汇报"),
            forbid_generic_prompt=True,
            require_context_hit=True,
            min_answer_length=110,
        ),
        Case(
            name="18床低氧监测计划",
            user_input=(
                "只作为单模型回答，不走复杂协作。针对18床目前低氧风险和二氧化碳潴留风险，给我一个班内监测计划："
                "先列马上要看什么，再列持续观察什么，再列什么情况下必须马上找医生。"
            ),
            mode="single_model",
            selected_model="minicpm3_4b_local",
            expect_workflows=("voice_inquiry",),
            expected_keywords=("血氧", "持续观察", "找医生"),
            min_answer_length=100,
        ),
        Case(
            name="单模型通用压伤预防",
            user_input=(
                "不要结合具体患者，只按通用护理知识回答：对于高龄、活动受限、营养差又合并失禁风险的住院患者，"
                "压伤预防应该从体位、皮肤检查、床单位、营养、家属宣教和记录留痕这几个方面怎么做？"
            ),
            mode="single_model",
            selected_model="minicpm3_4b_local",
            expect_workflows=("voice_inquiry",),
            expected_keywords=("压伤", "体位", "皮肤"),
            forbid_bed_requirement=True,
            min_answer_length=110,
        ),
        Case(
            name="病区下一班必须闭环事项",
            user_input=(
                "站在病区责任护士的视角，把今天交给下一班前必须闭环的事项分成四类：医嘱执行、风险复核、文书补录、医生沟通。"
                "不要只列标题，要结合当前病区的高风险患者举例说明哪些床位最需要优先闭环，以及为什么。"
            ),
            execution_profile="observe",
            expect_workflows=("voice_inquiry", "recommendation", "autonomous_care"),
            expected_keywords=("闭环", "医嘱执行", "文书补录"),
            min_answer_length=120,
        ),
        Case(
            name="16床19床22床联合医生沟通摘要",
            user_input=(
                "我要向医生团队做一次集中汇报，请把16床、19床和22床分别压缩成“当前最危险点、已完成护理动作、"
                "仍需医生决策”三句话，然后再补一段总括，说明这三床为什么是今天病区最不适合延迟处理的对象。"
            ),
            execution_profile="escalate",
            expect_workflows=("recommendation", "voice_inquiry"),
            expected_keywords=("16床", "19床", "22床"),
            min_answer_length=130,
        ),
        Case(
            name="手术物品清点记录规范问答",
            user_input=(
                "我现在不是要生成某个手术患者的记录，而是要确认规范。请详细说明手术物品清点记录的填写原则、清点时机、"
                "双人唱点和即时记录要求、术中追加物品如何记录、发现数量或完整性不符时应该怎么处理。"
            ),
            execution_profile="document",
            expect_workflows=("document_generation",),
            expected_keywords=("双人", "即时记录", "清点时机"),
            forbid_bed_requirement=True,
            min_answer_length=120,
        ),
        Case(
            name="体温单补录与异常标记规范",
            user_input=(
                "按护理文书书写基本规范，系统讲一遍体温单最容易漏填和最容易写错的地方，尤其是体温骤升骤降后的复测标记、"
                "降温后虚线连接、35℃以下不升的写法、脉搏与体温相遇、短绌脉绘制以及大便和疼痛栏位怎么记。"
            ),
            execution_profile="document",
            expect_workflows=("document_generation",),
            expected_keywords=("复测", "降温后", "不升"),
            forbid_bed_requirement=True,
            min_answer_length=120,
        ),
        Case(
            name="通用低血糖紧急处理问答",
            user_input=(
                "不要结合具体患者，只按临床护理常规详细说清楚住院患者出现疑似低血糖时护士应该怎么做："
                "先确认什么、先处理什么、多久复测一次、什么时候要准备静脉葡萄糖、"
                "以及处理后护理记录里最容易漏掉哪些关键点。"
            ),
            mode="single_model",
            selected_model="minicpm3_4b_local",
            expect_workflows=("voice_inquiry",),
            expected_keywords=("低血糖", "复测", "葡萄糖"),
            forbid_bed_requirement=True,
            min_answer_length=110,
        ),
        Case(
            name="通用夜班跌倒预防问答",
            user_input=(
                "按住院护理真实场景回答：高龄、夜尿频繁、步态不稳又刚用过镇静药的患者，"
                "夜班跌倒预防应该从床栏、呼叫铃、陪护提醒、起夜协助和交接班提醒几个方面怎么做，"
                "并说明哪些情况要立即升级为高风险重点交班。"
            ),
            mode="single_model",
            selected_model="minicpm3_4b_local",
            expect_workflows=("voice_inquiry",),
            expected_keywords=("跌倒", "床栏", "夜间"),
            forbid_bed_requirement=True,
            min_answer_length=110,
        ),
        Case(
            name="通用导尿管护理与感染预防",
            user_input=(
                "请按专业护理问答口径说明留置导尿患者日常护理要点，重点讲清楚导尿管固定、引流袋位置、尿道口清洁、"
                "尿液颜色性状观察、什么时候怀疑导尿相关感染，以及交班时必须交代的风险提示。"
            ),
            mode="single_model",
            selected_model="minicpm3_4b_local",
            expect_workflows=("voice_inquiry",),
            expected_keywords=("导尿管", "引流", "感染"),
            forbid_bed_requirement=True,
            min_answer_length=110,
        ),
        Case(
            name="通用氧疗与二氧化碳潴留观察",
            user_input=(
                "请按临床护理实操说明慢阻肺或二氧化碳潴留风险患者吸氧时护士应注意什么，"
                "包括氧疗方式、目标血氧范围、呼吸频率和意识变化观察，以及什么时候要警惕二氧化碳潴留加重。"
            ),
            mode="single_model",
            selected_model="minicpm3_4b_local",
            expect_workflows=("voice_inquiry",),
            expected_keywords=("氧疗", "血氧", "二氧化碳"),
            forbid_bed_requirement=True,
            min_answer_length=110,
        ),
        Case(
            name="12床夜班观察与升级阈值",
            user_input=(
                "如果今晚我负责12床，只有我一个责任护士，请按夜班工作顺序告诉我先观察什么、"
                "什么指标要30分钟内复核、尿量和血压出现到什么程度必须立刻联系医生，"
                "最后再给我一句适合写进夜班交班本的提醒。"
            ),
            execution_profile="observe",
            expect_bed="12",
            expect_workflows=("voice_inquiry", "recommendation"),
            expected_keywords=("12床", "尿量", "联系医生"),
            forbid_generic_prompt=True,
            require_context_hit=True,
            min_answer_length=120,
        ),
        Case(
            name="18床低氧电话SBAR",
            user_input=(
                "请针对18床当前低氧风险和二氧化碳潴留风险，给我整理一段可直接电话联系医生的SBAR式上报，"
                "包括现状、背景、我已做的处理、希望医生明确什么，以及医生可能追问我哪些数据。"
            ),
            execution_profile="escalate",
            expect_bed="18",
            expect_workflows=("recommendation", "voice_inquiry"),
            expected_keywords=("18床", "血氧", "医生"),
            forbid_generic_prompt=True,
            require_context_hit=True,
            min_answer_length=120,
        ),
        Case(
            name="23床恶露与贫血交班草稿",
            user_input=(
                "请把23床围绕恶露观察、贫血风险、感染警讯和生命体征趋势，生成一段能直接放进护理交接班报告的草稿。"
                "要明确本班已观察到什么、下一班最该盯什么，以及什么变化要升级汇报。"
            ),
            execution_profile="document",
            expect_bed="23",
            expect_workflows=("handover_generate", "document_generation"),
            expected_keywords=("23床", "恶露", "下一班"),
            forbid_generic_prompt=True,
            require_context_hit=True,
            min_answer_length=110,
        ),
        Case(
            name="16床血糖感染监测与补录顺序",
            user_input=(
                "请围绕16床的血糖波动和感染扩散风险，先列出本班监测顺序和时间点，"
                "再补一段文书补录建议，告诉我血糖记录、感染观察和医生沟通这三类内容应该按什么先后顺序留痕最稳妥。"
            ),
            execution_profile="document",
            expect_bed="16",
            expect_workflows=("document_generation", "recommendation"),
            expected_keywords=("16床", "血糖", "补录"),
            forbid_generic_prompt=True,
            require_context_hit=True,
            min_answer_length=120,
        ),
        Case(
            name="病区夜班双护士闭环任务",
            user_input=(
                "请按AI Agent闭环方式为今晚这个病区安排任务，只剩两名护士时，先排出最高优先级患者，"
                "再说明准备怎么分配巡查、哪些动作要先人工批准、哪些需要同步通知医生、"
                "最后要沉淀哪些交班和文书结果。"
            ),
            execution_profile="full_loop",
            expect_workflows=("autonomous_care",),
            expected_keywords=("病区", "人工批准", "交班"),
            min_answer_length=130,
        ),
        Case(
            name="12床18床通知医生并留痕",
            user_input=(
                "请把12床和18床作为本班重点，启动一轮AI Agent协作：先比较谁更急，"
                "再准备通知值班医生的摘要，同时把后续需要补的护理记录和交班内容一起列入闭环计划。"
            ),
            execution_profile="full_loop",
            expect_workflows=("autonomous_care",),
            expected_keywords=("12床", "18床", "人工批准"),
            require_context_hit=True,
            min_answer_length=130,
        ),
        Case(
            name="通用输液外渗处理问答",
            user_input=(
                "请按护理实操回答输液外渗时护士应该立刻怎么做，尤其是停止输液、保留通路、评估外渗范围、抬高患肢、"
                "冷热敷选择和后续记录留痕分别怎么处理，并说明哪些情况要马上通知医生。"
            ),
            mode="single_model",
            selected_model="minicpm3_4b_local",
            expect_workflows=("voice_inquiry",),
            expected_keywords=("外渗", "停止输液", "抬高"),
            forbid_bed_requirement=True,
            min_answer_length=110,
        ),
        Case(
            name="通用术后引流观察问答",
            user_input=(
                "请按专业护理问答说明术后带引流管患者应如何观察，重点说清引流量、颜色、性状、通畅性、"
                "固定和无菌护理，以及哪些变化提示需要立即联系医生。"
            ),
            mode="single_model",
            selected_model="minicpm3_4b_local",
            expect_workflows=("voice_inquiry",),
            expected_keywords=("引流", "颜色", "性状"),
            forbid_bed_requirement=True,
            min_answer_length=110,
        ),
        Case(
            name="交接班外出请假记录要求",
            user_input=(
                "请按护理日夜交接班报告规范回答：患者外出请假时，交接班里至少要记录哪些内容，"
                "尤其是去向、请假时间、医生意见、告知内容和回病区后的再评估要怎么交代。"
            ),
            execution_profile="observe",
            expect_workflows=("handover_generate", "document_generation", "voice_inquiry"),
            expected_keywords=("去向", "请假时间", "医生意见"),
            forbid_bed_requirement=True,
            min_answer_length=120,
        ),
        Case(
            name="体温单短绌脉绘制规范",
            user_input=(
                "按体温单书写规范详细说明短绌脉在体温单上应该怎么画，"
                "包括心率和脉搏分别用什么符号、两人同时测量的要求、"
                "相连方式以及图像中斜线区域应该怎么体现。"
            ),
            execution_profile="document",
            expect_workflows=("document_generation",),
            expected_keywords=("短绌脉", "心率", "脉搏"),
            forbid_bed_requirement=True,
            min_answer_length=120,
        ),
        Case(
            name="输血护理记录监测节点规范",
            user_input=(
                "请按输血护理记录书写要求讲清楚每袋血液输注前后应该在什么时间点测生命体征，"
                "尤其是输血开始前60分钟内、最初15分钟和结束后60分钟内这些节点如何记录，"
                "以及双人核对和输血反应观察为什么不能漏。"
            ),
            execution_profile="document",
            expect_workflows=("document_generation",),
            expected_keywords=("15分钟", "60分钟", "双人核对"),
            forbid_bed_requirement=True,
            min_answer_length=120,
        ),
        Case(
            name="血糖POCT记录与复测规范",
            user_input=(
                "请按血糖测量记录单和血糖谱记录单的规范，说清楚POCT记录时餐前、餐后、睡前、随机血糖、复测和签名这些内容怎么写，"
                "以及跨天跨月时日期栏怎么处理。"
            ),
            execution_profile="document",
            expect_workflows=("document_generation",),
            expected_keywords=("POCT", "复测", "餐前"),
            forbid_bed_requirement=True,
            min_answer_length=120,
        ),
        Case(
            name="19床22床集中汇报排序",
            user_input=(
                "请把19床和22床放在一次集中医生汇报里比较，先说明谁更需要优先上报，"
                "再分别压缩成一句现状、一句已处理、一句仍需医生决策，最后补一句总括说明排序理由。"
            ),
            execution_profile="escalate",
            expect_workflows=("recommendation", "voice_inquiry"),
            expected_keywords=("19床", "22床", "汇报"),
            require_context_hit=True,
            min_answer_length=120,
        ),
        Case(
            name="16床从监测到交班留痕闭环",
            user_input=(
                "请以AI Agent模式围绕16床做一轮完整闭环：先持续观察血糖和感染风险，"
                "再说明何时联系医生、何时需要人工批准、何时生成护理记录和交班草稿，"
                "最后给出一个本班结束前的闭环检查清单。"
            ),
            execution_profile="full_loop",
            expect_bed="16",
            expect_workflows=("autonomous_care",),
            expected_keywords=("持续", "人工批准", "留痕"),
            forbid_generic_prompt=True,
            require_context_hit=True,
            min_answer_length=130,
        ),
        Case(
            name="12床一般护理记录质控",
            user_input=(
                "请从文书质控角度检查12床一般护理记录草稿最容易缺什么，"
                "把病情观察、措施、效果、时间、签名和客观数据里最容易漏掉的字段分开列出来，"
                "并告诉我应该先补哪几个字段最重要。"
            ),
            execution_profile="document",
            expect_bed="12",
            expect_workflows=("document_generation",),
            expected_keywords=("12床", "缺失", "签名"),
            forbid_generic_prompt=True,
            require_context_hit=True,
            min_answer_length=120,
        ),
        Case(
            name="23床产后感染警讯与处理",
            user_input=(
                "请围绕23床产后恢复期目前的贫血和感染风险，告诉我本班最要紧的观察点，"
                "恶露、体温、心率和子宫复旧哪些变化要优先记录，哪些变化要立即联系医生，"
                "最后补一句适合写进交班报告的提醒。"
            ),
            execution_profile="observe",
            expect_bed="23",
            expect_workflows=("voice_inquiry", "recommendation"),
            expected_keywords=("23床", "恶露", "联系医生"),
            forbid_generic_prompt=True,
            require_context_hit=True,
            min_answer_length=120,
        ),
        Case(
            name="18床低氧升级处理阈值",
            user_input=(
                "请针对18床低氧风险，按临床护理实际回答：先看哪些指标判断只是继续观察还是需要升级处理，"
                "血氧、呼吸频率、意识和二氧化碳潴留风险各自到什么程度应该马上找医生。"
            ),
            execution_profile="observe",
            expect_bed="18",
            expect_workflows=("voice_inquiry", "recommendation"),
            expected_keywords=("18床", "血氧", "找医生"),
            forbid_generic_prompt=True,
            require_context_hit=True,
            min_answer_length=120,
        ),
        Case(
            name="通用谵妄与约束沟通问答",
            user_input=(
                "请按病房护理实务回答谵妄或意识混乱患者的安全护理要点，"
                "包括环境调整、家属陪护沟通、非药物安抚、约束适应证和记录留痕，"
                "并说明哪些情况不能只靠护士观察，必须及时请医生评估。"
            ),
            mode="single_model",
            selected_model="minicpm3_4b_local",
            expect_workflows=("voice_inquiry",),
            expected_keywords=("谵妄", "安全", "家属"),
            forbid_bed_requirement=True,
            min_answer_length=110,
        ),
        Case(
            name="通用心衰容量管理问答",
            user_input=(
                "不要绑定具体患者，请按心衰护理通用知识说明容量管理时护士最该连续关注什么，"
                "尤其是体重、尿量、下肢水肿、呼吸困难、入出量平衡和夜间症状，"
                "以及哪些变化提示不能再拖、要尽快联系医生。"
            ),
            mode="single_model",
            selected_model="minicpm3_4b_local",
            expect_workflows=("voice_inquiry",),
            expected_keywords=("心衰", "体重", "尿量"),
            forbid_bed_requirement=True,
            min_answer_length=110,
        ),
        Case(
            name="病区今班高危交班重点",
            user_input=(
                "请从病区整体视角整理今天这一班最值得交代给下一班的前五个高危重点，"
                "不仅要说是哪几床，还要说每床为什么危险、下一班要盯什么、哪类情况需要马上联系医生。"
            ),
            execution_profile="observe",
            expect_workflows=("voice_inquiry", "recommendation", "autonomous_care"),
            expected_keywords=("前五", "下一班", "联系医生"),
            min_answer_length=130,
        ),
        Case(
            name="16床19床22床联动协作闭环",
            user_input=(
                "请用AI Agent方式把16床、19床和22床做成一个联动协作任务：先排序，"
                "再给出医生沟通摘要、需要人工批准的动作、要生成的交班和文书留痕，"
                "最后输出一份本班收尾前的闭环清单。"
            ),
            execution_profile="full_loop",
            expect_workflows=("autonomous_care",),
            expected_keywords=("16床", "19床", "22床"),
            require_context_hit=True,
            min_answer_length=140,
        ),
    ]


def build_payload(case: Case, idx: int) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "mode": case.mode,
        "conversation_id": f"regression-{idx:02d}",
        "department_id": DEPARTMENT_ID,
        "requested_by": REQUESTED_BY,
        "workflow_type": "voice_inquiry",
        "user_input": case.user_input,
    }
    if case.mode == "agent_cluster":
        payload["cluster_profile"] = case.cluster_profile
    if case.mode == "single_model":
        payload["selected_model"] = case.selected_model or "minicpm3_4b_local"
    if case.execution_profile:
        payload["execution_profile"] = case.execution_profile
    return payload


def call_api(case: Case, idx: int) -> dict[str, Any]:
    payload = build_payload(case, idx)
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        API_URL,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        return json.load(resp)


def flatten_response(response: dict[str, Any]) -> str:
    parts: list[str] = []
    summary = str(response.get("summary") or "").strip()
    if summary:
        parts.append(summary)

    for item in response.get("findings", []) or []:
        if isinstance(item, dict):
            parts.append(str(item.get("title") or item.get("content") or "").strip())
        else:
            parts.append(str(item).strip())

    for item in response.get("recommendations", []) or []:
        if isinstance(item, dict):
            title = str(item.get("title") or "").strip()
            rationale = str(item.get("rationale") or "").strip()
            if title:
                parts.append(title)
            if rationale:
                parts.append(rationale)
        else:
            parts.append(str(item).strip())

    for item in response.get("next_actions", []) or []:
        if isinstance(item, dict):
            title = str(item.get("title") or item.get("label") or "").strip()
            if title:
                parts.append(title)
        else:
            parts.append(str(item).strip())

    return "\n".join([part for part in parts if part])


def evaluate(case: Case, response: dict[str, Any]) -> tuple[bool, list[str]]:
    issues: list[str] = []
    summary = str(response.get("summary") or "")
    workflow_type = str(response.get("workflow_type") or "")
    bed_no = str(response.get("bed_no") or "")
    context_hit = response.get("context_hit")
    patient_name = str(response.get("patient_name") or "")
    patient_id = str(response.get("patient_id") or "")
    flattened = flatten_response(response)

    if case.expect_bed and bed_no != case.expect_bed:
        issues.append(f"未命中预期床号：期望 {case.expect_bed}，实际 {bed_no or '空'}")

    expected_workflows = [WORKFLOW_ALIASES.get(item, item) for item in case.expect_workflows]
    if case.mode == "single_model" and "single_model_chat" not in expected_workflows:
        expected_workflows.append("single_model_chat")
    if expected_workflows and workflow_type not in expected_workflows:
        issues.append(f"工作流类型不符：期望 {tuple(expected_workflows)}，实际 {workflow_type or '空'}")

    effective_context_hit = bool(context_hit) or bool(bed_no) or bool(patient_name) or bool(patient_id) or bool(
        re.search(r"\d{1,3}床", flattened)
    )

    if case.require_context_hit is True and not effective_context_hit:
        issues.append("没有命中患者上下文")
    if case.require_context_hit is False and effective_context_hit:
        issues.append("本应用通用问答，却错误绑定了病例上下文")

    if case.forbid_generic_prompt and any(marker in flattened for marker in GENERIC_MARKERS):
        issues.append("回答仍在要求补充床号/上下文，没有真正处理当前命令")

    if case.forbid_bed_requirement and any(marker in flattened for marker in GENERIC_MARKERS):
        issues.append("通用问题仍被错误要求补充床号")

    if len(flattened.strip()) < case.min_answer_length:
        issues.append(f"回答过短：仅 {len(flattened.strip())} 个字符")

    for keyword in case.expected_keywords:
        if keyword not in flattened:
            issues.append(f"缺少关键内容：{keyword}")

    for keyword in case.forbid_keywords:
        if keyword in flattened:
            issues.append(f"出现不应出现的内容：{keyword}")

    if not summary.strip():
        issues.append("summary 为空")

    return (len(issues) == 0, issues)


def main() -> int:
    cases = build_cases()
    rows: list[dict[str, Any]] = []
    pass_count = 0
    started_all = time.time()

    for idx, case in enumerate(cases, start=1):
        started = time.time()
        try:
            response = call_api(case, idx)
            passed, issues = evaluate(case, response)
            if passed:
                pass_count += 1
            rows.append(
                {
                    "index": idx,
                    "name": case.name,
                    "passed": passed,
                    "issues": issues,
                    "elapsed_sec": round(time.time() - started, 2),
                    "workflow_type": response.get("workflow_type"),
                    "bed_no": response.get("bed_no"),
                    "patient_name": response.get("patient_name"),
                    "context_hit": response.get("context_hit"),
                    "summary": response.get("summary"),
                    "findings": response.get("findings"),
                    "recommendations": response.get("recommendations"),
                }
            )
        except urllib.error.HTTPError as err:
            rows.append(
                {
                    "index": idx,
                    "name": case.name,
                    "passed": False,
                    "issues": [f"HTTP {err.code}"],
                    "elapsed_sec": round(time.time() - started, 2),
                    "error_body": err.read().decode("utf-8", "ignore"),
                }
            )
        except Exception as err:  # noqa: BLE001
            rows.append(
                {
                    "index": idx,
                    "name": case.name,
                    "passed": False,
                    "issues": [f"{type(err).__name__}: {err}"],
                    "elapsed_sec": round(time.time() - started, 2),
                }
            )

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        json.dumps(
            {
                "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "api_url": API_URL,
                "total": len(cases),
                "passed": pass_count,
                "failed": len(cases) - pass_count,
                "elapsed_sec": round(time.time() - started_all, 2),
                "results": rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(
        json.dumps(
            {
                "total": len(cases),
                "passed": pass_count,
                "failed": len(cases) - pass_count,
                "report": str(REPORT_PATH),
            },
            ensure_ascii=False,
        )
    )
    failed_rows = [row for row in rows if not row["passed"]]
    if failed_rows:
        preview = [
            {
                "index": row.get("index"),
                "name": row.get("name"),
                "workflow_type": row.get("workflow_type"),
                "issues": row.get("issues", []),
                "summary": str(row.get("summary") or "")[:240],
            }
            for row in failed_rows[:12]
        ]
        print(json.dumps(preview, ensure_ascii=False, indent=2))
    return 0 if pass_count == len(cases) else 1


if __name__ == "__main__":
    raise SystemExit(main())
