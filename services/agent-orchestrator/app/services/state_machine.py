from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from typing import Any

import httpx

from app.core.config import settings
from app.schemas.workflow import (
    AgentStep,
    PatientScopePreview,
    ResolvedScopePatient,
    WorkflowOutput,
    WorkflowRequest,
    WorkflowType,
)
from app.services.agentic_orchestrator import agentic_orchestrator, is_autonomous_request
from app.services.history_store import workflow_history_store
from app.services.llm_client import bailian_refine, local_refine_with_model


class AgentStateMachine:
    HANDOVER_TOKENS = ("交班", "交接班", "handover", "shift")
    DOCUMENT_TOKENS = (
        "文书",
        "草稿",
        "护理记录",
        "病程记录",
        "体温单",
        "记录单",
        "清点记录",
        "病危护理记录",
        "病重护理记录",
        "输血护理记录",
        "血糖测量记录",
        "血糖谱",
        "血糖记录单",
        "模板",
        "字段",
        "半结构化",
        "审核",
        "归档",
        "归档预览",
        "归档床位",
        "模板正文预览",
        "word",
        "excel",
        "中医护理效果评价表",
        "效果评价",
        "辨证施护",
        "手术物品清单",
        "健康教育记录",
        "出院宣教",
        "document",
        "draft",
    )
    RECOMMEND_TOKENS = ("建议", "优先级", "风险", "上报", "升级", "recommend", "escalate", "triage")
    WARD_TOKENS = ("病区", "全病区", "全部患者", "所有患者", "整体", "排序", "排优先级")
    GLOBAL_SCOPE_TOKENS = ("整个数据库", "全库", "所有床位", "全部床位", "全体患者", "数据库里所有患者", "全院")
    COLLAB_TOKENS = (
        "发给",
        "发送给",
        "通知",
        "联系",
        "协作",
        "转告",
        "值班医生",
        "责任医生",
        "护士长",
        "住院医",
        "send",
        "notify",
        "doctor on duty",
    )
    CN_DIGIT_MAP: dict[str, int] = {
        "零": 0,
        "〇": 0,
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
    }
    CN_UNIT_MAP: dict[str, int] = {
        "十": 10,
        "百": 100,
    }
    MOJIBAKE_MARKERS: tuple[str, ...] = ("鍖", "鐥", "鎶", "璇", "闂", "锟", "Ã", "�", "?")
    GENERAL_ASSISTANT_SYSTEM = (
        "你是临床护理 AI 助手。"
        "当用户没有绑定具体患者时，请直接回答通用护理、中医问诊、文书流程、系统能力和工作流设计问题。"
        "不要强行要求床号；只有在确实需要病例级信息时，才补充说明“如果要结合具体患者，请再告诉我床号或病区”。"
        "回答要简洁、可执行、符合临床护理场景。"
    )
    TCM_ASSISTANT_SYSTEM = (
        "你是临床护理场景中的中医护理辅助模型。"
        "请优先输出：1) 可能的证候线索 2) 护理观察重点 3) 需要立即转医生的风险。"
        "结论仅用于护理辅助，不能替代医师诊断和处方。"
    )

    @staticmethod
    def _ensure_question(summary: str, question: str | None) -> str:
        s = (summary or "").strip()
        q = (question or "").strip()
        if s:
            return s
        return q

    @staticmethod
    def _strip_prompt_scaffold(question: str | None) -> str:
        q = (question or "").strip()
        if not q:
            return ""
        scaffold_fragments = (
            "请把下面内容当成护士在真实临床班次里一次性交给 AI Agent 的长任务",
            "我希望你像一个真正能服务临床的护理协作系统一样",
            "如果你要输出交接班、护理记录、待办清单或文书草稿",
            "请不要只给概念性建议，而要像真正的临床护理 AI Agent 一样",
            "如果涉及一般临床问题，请像带教老师一样直接作答",
        )
        kept: list[str] = []
        for raw in q.splitlines():
            line = raw.strip()
            if not line:
                continue
            if any(fragment in line for fragment in scaffold_fragments):
                continue
            kept.append(line)
        cleaned = "\n".join(kept).strip()
        return cleaned or q

    @staticmethod
    def _llm_unavailable(text: str | None) -> bool:
        t = (text or "").strip()
        if not t:
            return True
        markers = (
            "本地模型当前不可用",
            "请先启动本地中文模型服务",
            "当前模型调用失败",
            "禁止云端回退",
        )
        return any(m in t for m in markers)

    @staticmethod
    def _normalize_user_id(value: str | None) -> str:
        raw = (value or "").strip()
        if not raw:
            return "u_linmeili"
        if raw.startswith("u_"):
            return raw
        return f"u_{raw}"

    @classmethod
    def _parse_cn_number(cls, token: str) -> int | None:
        raw = (token or "").strip()
        if not raw:
            return None
        raw = raw.removeprefix("第")
        if not raw:
            return None
        if raw.isdigit():
            value = int(raw)
            return value if 1 <= value <= 199 else None
        if any(ch not in cls.CN_DIGIT_MAP and ch not in cls.CN_UNIT_MAP for ch in raw):
            return None
        if not any(ch in cls.CN_UNIT_MAP for ch in raw):
            digits: list[str] = []
            for ch in raw:
                if ch not in cls.CN_DIGIT_MAP:
                    return None
                digits.append(str(cls.CN_DIGIT_MAP[ch]))
            if not digits:
                return None
            value = int("".join(digits))
            return value if 1 <= value <= 199 else None

        total = 0
        current = 0
        for ch in raw:
            if ch in cls.CN_DIGIT_MAP:
                current = cls.CN_DIGIT_MAP[ch]
                continue
            unit = cls.CN_UNIT_MAP.get(ch)
            if unit is None:
                return None
            if current == 0:
                current = 1
            total += current * unit
            current = 0
        total += current
        return total if 1 <= total <= 199 else None

    @staticmethod
    def _parse_bed_no(raw: str) -> str | None:
        value = AgentStateMachine._parse_cn_number(raw)
        if value is None:
            return None
        return str(value)

    @classmethod
    def _bed_sort_key(cls, bed_no: str) -> tuple[int, int, str]:
        value = cls._parse_cn_number(str(bed_no or "").strip())
        if value is None:
            return (1, 9999, str(bed_no or ""))
        return (0, value, str(bed_no or ""))

    @classmethod
    def _extract_bed_nos_from_rows(cls, rows: Any) -> list[str]:
        if not isinstance(rows, list):
            return []
        out: list[str] = []
        seen: set[str] = set()
        for item in rows:
            if not isinstance(item, dict):
                continue
            raw = str(item.get("bed_no") or "").strip()
            if not raw:
                continue
            bed = cls._parse_bed_no(raw) or raw
            if bed and bed not in seen:
                seen.add(bed)
                out.append(bed)
        out.sort(key=cls._bed_sort_key)
        return out

    @classmethod
    def _resolve_nearest_bed(
        cls,
        requested_bed: str,
        available_beds: list[str],
        *,
        max_distance: int = 2,
    ) -> str | None:
        requested = cls._parse_cn_number(str(requested_bed or "").strip())
        if requested is None:
            return None
        if not available_beds:
            return None

        best: tuple[int, int, str] | None = None
        for bed in available_beds:
            normalized = str(bed or "").strip()
            parsed = cls._parse_cn_number(normalized)
            if parsed is None:
                continue
            diff = abs(parsed - requested)
            candidate = (diff, parsed, normalized)
            if best is None or candidate < best:
                best = candidate
                if diff == 0:
                    break
        if best is None:
            return None
        if best[0] > max_distance:
            return None
        return best[2]

    @staticmethod
    def _extract_beds(text: str | None) -> list[str]:
        q = (text or "").strip()
        if not q:
            return []
        out: list[str] = []
        seen: set[str] = set()

        def add(raw: str) -> None:
            bed = AgentStateMachine._parse_bed_no(raw)
            if not bed:
                return
            if bed not in seen:
                seen.add(bed)
                out.append(bed)

        patterns = (
            r"(?<!\d)(\d{1,3})\s*(?:床|号床|床位)",
            r"(?:第)?([零〇一二两三四五六七八九十百]{1,5})\s*(?:床|号床|床位)",
            r"\bbed\s*(\d{1,3})\b",
            r"\b(\d{1,3})\s*bed\b",
        )
        for pattern in patterns:
            for match in re.finditer(pattern, q, flags=re.IGNORECASE):
                add(match.group(1))
        if out:
            return out

        non_bed_units = ("类", "项", "名", "条", "次", "个", "分钟", "小时", "天", "周", "月", "年", "%", "例")

        def iter_loose_numbers() -> list[str]:
            values: list[str] = []
            seen_values: set[str] = set()
            for match in re.finditer(r"(?<!\d)(\d{1,3})(?!\d)", q):
                start, end = match.span(1)
                left = q[max(0, start - 3):start]
                right = q[end:end + 3]
                line_start = start == 0 or q[start - 1] == "\n"
                if line_start and right[:1] in (".", "、", ")", "）", ":"):
                    continue
                if any(unit in left or unit in right for unit in non_bed_units):
                    continue
                token = match.group(1)
                if token not in seen_values:
                    seen_values.add(token)
                    values.append(token)
            return values

        multi_bed_scope = any(
            token in q
            for token in ("一起看", "一起分析", "同时看", "同时分析", "比较", "对比", "分别", "多床", "多个床", "哪几床", "几床")
        )
        if multi_bed_scope:
            for token in iter_loose_numbers()[:6]:
                add(token)
        if out:
            return out

        single_bed_scope = any(token in q for token in ("帮我看", "看看", "分析", "关注", "定位", "核对", "复盘", "总结")) and any(
            token in q for token in ("患者", "病人", "床旁", "病例", "档案", "床位", "床号")
        )
        if single_bed_scope:
            loose_numbers = iter_loose_numbers()
            if len(loose_numbers) == 1:
                add(loose_numbers[0])
        if out:
            return out

        # Fallback for garbled speech text where Chinese tokens are lost
        # but bed numbers survive (e.g. "????12????").
        mojibake_score = sum(q.count(marker) for marker in AgentStateMachine.MOJIBAKE_MARKERS)
        if mojibake_score >= 2:
            numeric_tokens: list[str] = []
            seen_num: set[str] = set()
            for match in re.finditer(r"(?<!\d)(\d{1,3})(?!\d)", q):
                token = match.group(1)
                if token in seen_num:
                    continue
                seen_num.add(token)
                numeric_tokens.append(token)
            if len(numeric_tokens) == 1:
                add(numeric_tokens[0])
        return out

    @classmethod
    def _is_ward_scope(cls, question: str | None, beds: list[str] | None = None) -> bool:
        q = cls._strip_prompt_scaffold(question)
        low = q.lower()
        if cls._is_global_scope(q):
            return True
        if beds and len(beds) >= 2:
            return True
        # When user already specified one bed, keep single-patient scope unless
        # there is an explicit ward/global phrase.
        if beds and len(beds) == 1:
            if any(token in low for token in ("all beds", "all patients", "ward")):
                return True
            return any(token in q for token in cls.WARD_TOKENS)
        if any(token in low for token in ("all beds", "all patients", "ward", "priority", "triage")):
            return True
        return any(token in q for token in cls.WARD_TOKENS)

    @classmethod
    def _is_global_scope(cls, question: str | None) -> bool:
        q = cls._strip_prompt_scaffold(question)
        if not q:
            return False
        low = q.lower()
        return any(token in low for token in ("all hospital", "all database", "global")) or any(
            token in q for token in cls.GLOBAL_SCOPE_TOKENS
        )

    async def _answer_general_question(self, question: str) -> str:
        q = self._strip_prompt_scaffold(question)
        if self._is_system_design_query(q):
            if any(token in q for token in ("记忆机制", "连续追踪")):
                return self._ensure_question(
                    "AI Agent 的记忆机制不是单纯保存聊天记录，而是把上一次的风险、待办、人工复核结果、文书状态和医生沟通结论提炼成可检索的连续追踪摘要。"
                    "这样下一班再问时，系统会优先把今日待办、交接班重点、未闭环事项和需要人工复核的条目重新排到前面，同时区分旧事件和新变化，避免把历史问题误当成当前新增异常。"
                    "护士看到的不是散乱聊天，而是已经整理好的记忆卡片：哪些风险还在持续、哪些文书还没归档、哪些医生沟通已完成、哪些人工复核还要继续追踪。",
                    q,
                )
            if any(token in q for token in ("热力图", "时间轴", "看板", "可视化", "首页设计")):
                return self._ensure_question(
                    "首页最该保留三块：病区风险热力图、今日待办时间轴、交接班摘要看板。"
                    "病区风险热力图解决“谁最危险”，今日待办时间轴解决“现在先做什么”，交接班摘要看板解决“下一班必须知道什么”；护士看完后就能立刻按优先级行动，而不是先自己从一堆卡片里找重点。"
                    "这三块配合起来，能把风险判断、班内行动和下一班承接直接连成一条线，所以首页看完后应该马上行动，而不是再去不同页面里拼信息。",
                    q,
                )
            if any(token in q for token in ("普通大模型", "工作流", "效率提升", "为什么是 AI Agent", "为什么是AI Agent")):
                return self._ensure_question(
                    "普通大模型更像一次性问答，而临床 AI Agent 会把病区待办、风险分层、文书草稿、人工审核和归档串成闭环。"
                    "它先按风险分层决定优先级，再把结果同步到病区待办、交接班和文书草稿，最后经人工审核后归档，所以更适合护理场景的连续执行，而不只是回答一句建议。"
                    "临床效率提升不只是回答更快，而是少来回切模块、少漏待办、少漏交接、少漏人工复核，护士看到的就是已经排好优先级的行动链。",
                    q,
                )
        if "家属" in q and any(token in q for token in ("沟通", "告知", "留痕")) and not self._extract_beds(q):
            return self._ensure_question(
                "家属沟通时建议按“当前变化-已做处理-接下来观察什么-什么情况会联系医生”四步说清，既不要承诺过度，也不要只给空泛安慰。"
                "像低血压、低氧、输血反应和术后恢复这类问题，都要把目前看到的变化、已经采取的处理、接下来复评的时间点和升级阈值讲明白；"
                "关键告知内容、家属反馈和是否已联系医生要及时留痕，交接班也要写清已沟通内容、家属关注点和下一班继续解释或观察的重点。",
                q,
            )
        if ("病重" in q or "病危" in q) and any(token in q for token in ("频次", "多久", "几小时", "记录要求")):
            return self._ensure_question(
                "病重（病危）患者护理记录中的生命体征一般至少每4小时记录1次，体温若无特殊变化时至少每日测量4次；出入量要按班次小结，病情变化时应随时加记。"
                "书写时不仅要记生命体征和出入量，还要同步写病情变化、护理措施、效果评价以及下一班观察重点。"
                "最容易漏掉的是只写结果、不写护理措施和效果，所以带教时要提醒新人把时间点、处理经过和下一班继续观察点一起写完整。",
                q,
            )
        if any(token in q for token in ("胸闷", "胸痛", "胸部压榨痛", "胸骨后疼痛")) and not self._extract_beds(q):
            if self._is_tcm_question(q):
                return self._ensure_question(
                    "从中医护理角度可以辅助观察胸痹、气滞血瘀、痰浊痹阻或阳虚水泛等证候线索，但胸闷胸痛先按急症风险处理，不能只停留在辨证层面。"
                    "床旁先让患者停止活动、取相对安静体位，立即复测血压、脉搏、呼吸、血氧，观察疼痛部位、性质、持续时间、是否放射及有无大汗、恶心、面色改变。"
                    "如果胸痛持续不缓解、伴呼吸困难、血压下降、出冷汗、意识变化或血氧下降，应立即联系医生；中医护理可作为辅助调护与观察，不替代医生评估和处置。",
                    q,
                )
            return self._ensure_question(
                "胸闷胸痛先按高风险异常处理：立即停止活动，协助患者取相对舒适体位，马上复测血压、脉搏、呼吸、血氧，并问清疼痛部位、性质、持续时间和是否放射。"
                "若胸痛持续不缓解，或伴呼吸困难、血压下降、出冷汗、恶心呕吐、面色苍白、意识变化等情况，应立即联系医生并持续床旁观察。"
                "护理记录和交接班要写清异常开始时间、生命体征变化、已做处理、医生沟通时间及下一次复评时间点。",
                q,
            )
        if "低血压" in q and "少尿" in q and not self._extract_beds(q):
            return self._ensure_question(
                "低血压少尿时，床旁第一眼先看意识、皮肤温度和末梢灌注；5分钟内复核血压趋势、尿量和导尿通畅；30分钟内结合补液后反应继续看血压趋势、尿量变化和是否需要联系医生。"
                "若收缩压持续下降、尿量继续减少、末梢灌注变差或意识改变，应立即联系医生，并把已做处置、再评估结果和下一班继续观察点写入交接班。",
                q,
            )
        if ("低氧" in q or "氧饱和度" in q or ("翻身" in q and any(token in q for token in ("呼吸", "血氧", "氧合")))) and not self._extract_beds(q):
            return self._ensure_question(
                "低氧患者翻身前先看氧饱和度、呼吸频率、吸氧装置是否通畅；翻身中持续观察面色、主诉和监护波动；翻身后复评氧饱和度、呼吸频率和吸氧效果。"
                "若翻身过程中氧饱和度持续下降、呼吸困难明显加重或恢复慢，应暂停并升级处理，必要时立即联系医生。"
                "交接班时要补一句“翻身前后血氧和呼吸频率波动情况、是否暂停操作、何时再复评”，这样下一班就能直接承接。",
                q,
            )
        if any(token in q for token in ("压伤", "压力性损伤", "高风险")) and not self._extract_beds(q):
            return self._ensure_question(
                "压伤高风险患者今日护理重点要围绕翻身、皮肤观察、营养支持和潮湿管理来排班。"
                "交接班里要写清受压部位皮肤观察结果、翻身执行情况和下一班继续盯的点，护理记录里要留痕皮肤观察、减压措施和营养相关干预。"
                "真正落地时不要只写“注意压伤”，而要写清翻身频次、皮肤观察、营养支持执行情况和护理记录留痕。",
                q,
            )
        if "疼痛" in q and any(token in q for token in ("复评", "镇痛", "干预")) and not self._extract_beds(q):
            return self._ensure_question(
                "疼痛干预后要按药物途径安排疼痛复评：胃肠外给药通常在15-30分钟复评，口服镇痛药通常在1-2小时复评。"
                "复评结果既要体现在体温单疼痛栏，也要在护理记录中补充疼痛复评时间、分值变化和处理效果。"
                "如果复评后疼痛仍明显、伴生命体征波动或患者主诉加重，要及时再评估原因并通知医生，交接班也要把疼痛复评结果交代清楚。",
                q,
            )
        if any(token in q for token in ("导尿", "导尿管", "引流")) and not self._extract_beds(q):
            return self._ensure_question(
                "导尿与引流观察时，先看尿量或引流量，再看颜色性状和是否引流不畅，随后结合患者症状判断是否需要联系医生。"
                "交接班应写清尿量、颜色性状、导管通畅度、已做复核和下一班继续观察点，若出现尿量骤减、鲜红引流增多或管路不畅，应及时联系医生。"
                "护理记录里最好把异常开始时间、已做冲管或复核动作、患者不适主诉和升级阈值一起写明，避免下一班重复判断。",
                q,
            )

        prompt = (
            "请直接回答下面这个护理场景中的通用问题。"
            "如果问题涉及系统能力、流程设计、中医护理思路或文书使用方式，请先直接解释，"
            "再用一句话提醒：需要结合具体患者时可补充床号。\n"
            f"用户问题：{question}"
        )

        refined: str | None = None
        if settings.local_only_mode:
            refined = await local_refine_with_model(
                prompt,
                settings.local_llm_model_primary,
                system_msg=self.GENERAL_ASSISTANT_SYSTEM,
            )
        else:
            refined = await bailian_refine(prompt)

        if self._llm_unavailable(refined):
            return self._ensure_question(
                "这是一个通用问题，我可以直接回答，不必先绑定病例。"
                "如果你问的是护理流程、文书使用、中医辨证思路或系统怎么工作，我会先按通用知识说明；"
                "需要贴合具体患者时，我再继续细化到对应档案。",
                question,
            )
        return self._ensure_question(refined, question)

    @staticmethod
    def _is_tcm_question(question: str | None) -> bool:
        q = AgentStateMachine._strip_prompt_scaffold(question)
        if not q:
            return False
        strong_tokens = (
            "中医",
            "中西医结合",
            "辨证",
            "辨证施护",
            "证候",
            "证型",
            "舌",
            "舌象",
            "舌苔",
            "脉",
            "脉象",
            "痰湿",
            "阳虚",
            "气虚",
            "水湿",
            "情志",
            "饮食护理",
            "中医护理",
            "从中医护理角度",
        )
        if any(token in q for token in strong_tokens):
            return True
        supportive_tokens = ("气短", "乏力", "浮肿", "痰多", "纳差", "失眠")
        return sum(1 for token in supportive_tokens if token in q) >= 2 and any(
            token in q for token in ("护理", "调护", "带教", "观察")
        )

    @staticmethod
    def _is_explicit_no_patient_query(question: str | None) -> bool:
        q = AgentStateMachine._strip_prompt_scaffold(question)
        if not q:
            return False
        return any(
            token in q
            for token in (
                "不针对具体患者",
                "不涉及具体患者",
                "不针对任何患者",
                "不针对单一患者",
                "不针对单个患者",
                "不针对某个患者",
                "不针对某位患者",
                "不针对某一患者",
                "不指定患者",
                "不结合具体患者",
                "不结合具体病例",
                "不针对具体病例",
                "不针对具体床位",
                "不针对单床位",
                "不要要求补床号",
                "不要补床号",
                "不用补床号",
                "只是问规范",
                "只是问流程",
                "只是问设计",
                "通用问题",
                "一般问题",
                "培训场景",
                "带教场景",
            )
        )

    @staticmethod
    def _is_system_design_query(question: str | None) -> bool:
        q = AgentStateMachine._strip_prompt_scaffold(question)
        if not q:
            return False
        strong_design_tokens = (
            "记忆机制",
            "连续追踪",
            "为什么是 AI Agent",
            "为什么是AI Agent",
            "普通大模型",
            "产品介绍",
            "比赛答辩",
            "系统价值",
            "效率提升",
            "首页设计",
        )
        if any(token in q for token in strong_design_tokens):
            return True

        visual_tokens = ("病区风险热力图", "今日待办时间轴", "交接班摘要看板", "可视化")
        if not any(token in q for token in visual_tokens):
            return False

        design_intent_tokens = (
            "解释",
            "设计",
            "为什么",
            "价值",
            "解决什么临床问题",
            "切中护理痛点",
            "产品介绍",
            "比赛答辩",
            "立刻采取什么动作",
            "不是信息堆砌",
        )
        clinical_execution_tokens = (
            "体温单",
            "输血护理记录",
            "病重护理记录",
            "低血压",
            "少尿",
            "低氧",
            "翻身",
            "疼痛",
            "导尿",
            "引流",
            "压伤",
            "家属沟通",
            "模板导入",
            "待补字段",
            "自动回填",
            "患者档案",
            "巡查顺序",
            "观察重点",
            "升级",
            "双人核对",
            "15分钟",
            "60分钟",
            "出入量",
            "血糖",
            "POCT",
            "归档",
        )
        return any(token in q for token in design_intent_tokens) and not any(token in q for token in clinical_execution_tokens)

    async def _answer_tcm_question(self, question: str, context: dict[str, Any] | None = None) -> str:
        prompt_parts = [
            "请从中医护理辅助角度回答，输出证候线索、护理观察重点、立即转医生风险。",
        ]
        if context:
            prompt_parts.append(f"患者摘要：{self._build_single_patient_summary(context, context.get('bed_no'))}")
            prompt_parts.append(f"风险标签：{'、'.join(str(item).strip() for item in context.get('risk_tags', []) if str(item).strip())}")
        prompt_parts.append(f"用户问题：{question}")
        prompt = "\n".join(part for part in prompt_parts if part)

        refined = await local_refine_with_model(
            prompt,
            settings.local_llm_model_tcm,
            system_msg=self.TCM_ASSISTANT_SYSTEM,
        )
        if self._llm_unavailable(refined):
            refined = await local_refine_with_model(
                prompt,
                settings.local_llm_model_primary,
                system_msg=self.TCM_ASSISTANT_SYSTEM,
            )
        if self._llm_unavailable(refined):
            if any(token in question for token in ("胸闷", "胸痛", "胸部压榨痛", "胸骨后疼痛")):
                return self._ensure_question(
                    "从中医护理角度可先关注胸痹相关证候线索，如气滞血瘀、痰浊痹阻、气虚血瘀或阳虚水泛，但胸闷胸痛必须先按急症风险处理。"
                    "护理观察重点是疼痛部位和性质、持续时间、是否放射、是否伴大汗恶心、呼吸困难、血压下降和血氧波动；同时留意舌象、脉象、寒热偏向和情志变化。"
                    "若胸痛持续不缓解，或伴呼吸困难、血压下降、出冷汗、意识变化、血氧下降，应立即联系医生；中医护理只能作为辅助观察和调护，不能替代医生评估。",
                    question,
                )
            return self._ensure_question(
                "从中医护理角度，可先关注气虚水饮或心肾阳虚等证候线索。"
                "观察重点放在气短乏力是否加重、浮肿范围、尿量变化、睡眠与喘憋、舌苔和寒热表现；"
                "如果出现血压继续下降、呼吸困难加重、胸闷胸痛、少尿或意识变化，应立即联系医生并转医生处理。"
                "需要结合具体辨证时，我也可以继续细化到对应患者档案。",
                question,
            )
        return self._ensure_question(refined, question)

    @staticmethod
    def _risk_score(context: dict[str, Any]) -> int:
        risk_tags = context.get("risk_tags") if isinstance(context.get("risk_tags"), list) else []
        pending_tasks = context.get("pending_tasks") if isinstance(context.get("pending_tasks"), list) else []
        observations = context.get("latest_observations") if isinstance(context.get("latest_observations"), list) else []

        def parse_number(raw: Any) -> float | None:
            if raw is None:
                return None
            matched = re.search(r"-?\d+(?:\.\d+)?", str(raw))
            if not matched:
                return None
            try:
                return float(matched.group(0))
            except ValueError:
                return None

        def text_bonus(text: str) -> int:
            normalized = str(text or "").lower()
            bonus = 0
            if any(token in normalized for token in ("低氧", "spo2", "血氧", "呼吸困难", "紫绀")):
                bonus += 3
            if any(token in normalized for token in ("低血压", "少尿", "尿量减少", "休克")):
                bonus += 3
            if any(token in normalized for token in ("胸痛", "胸闷", "意识改变", "昏迷", "抽搐")):
                bonus += 3
            if any(token in normalized for token in ("输血", "寒战", "发热", "过敏反应")):
                bonus += 2
            if any(token in normalized for token in ("跌倒", "躁动", "压伤", "感染", "导管")):
                bonus += 1
            if any(token in normalized for token in ("上报医生", "联系医生", "立即复核", "立即处理")):
                bonus += 2
            return bonus

        score = len(risk_tags) * 2 + len(pending_tasks)
        score += sum(text_bonus(item) for item in risk_tags[:4])
        score += sum(text_bonus(item) for item in pending_tasks[:4])

        abnormal = 0
        for obs in observations[:8]:
            if not isinstance(obs, dict):
                continue
            flag = str(obs.get("abnormal_flag") or "").lower()
            name = str(obs.get("name") or "").strip().lower()
            value = parse_number(obs.get("value"))
            if flag and flag not in {"normal", "ok", "none"}:
                abnormal += 1
            score += text_bonus(name)

            if value is None:
                continue
            if "spo2" in name or "血氧" in name:
                if value < 90:
                    score += 5
                elif value < 93:
                    score += 3
            elif "收缩压" in name or "systolic" in name:
                if value < 90 or value >= 180:
                    score += 5
                elif value < 100 or value >= 160:
                    score += 3
            elif "呼吸" in name:
                if value >= 28 or value <= 10:
                    score += 3
            elif "尿量" in name:
                if value < 30:
                    score += 3
            elif "疼痛" in name and value >= 7:
                score += 2
            elif ("血糖" in name or "glucose" in name) and (value >= 16 or value < 3.9):
                score += 2
            elif "nihss" in name and value >= 8:
                score += 3

        return score + abnormal

    @staticmethod
    def _context_priority_reason(context: dict[str, Any]) -> str:
        risk_tags = [str(item).strip() for item in context.get("risk_tags", [])[:2] if str(item).strip()]
        pending_tasks = [str(item).strip() for item in context.get("pending_tasks", [])[:2] if str(item).strip()]
        abnormal: list[str] = []
        for item in context.get("latest_observations", [])[:4]:
            if not isinstance(item, dict):
                continue
            flag = str(item.get("abnormal_flag") or "").strip()
            if not flag or flag.lower() in {"normal", "ok", "none"}:
                continue
            name = str(item.get("name") or "").strip()
            value = str(item.get("value") or "").strip()
            abnormal.append(f"{name}{value}".strip())

        reasons: list[str] = []
        if abnormal:
            reasons.append(f"异常指标：{'；'.join(abnormal[:2])}")
        if risk_tags:
            reasons.append(f"风险标签：{'、'.join(risk_tags)}")
        if pending_tasks:
            reasons.append(f"待处理：{'、'.join(pending_tasks)}")
        return "；".join(reasons) or "需结合最新生命体征和待处理任务继续复核"

    @staticmethod
    def _normalize_recommendations(raw: Any, fallback: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        if isinstance(raw, list):
            for item in raw[:8]:
                if isinstance(item, dict):
                    title = str(item.get("title") or item.get("action") or "").strip()
                    if title:
                        output.append({"title": title, "priority": int(item.get("priority", 2) or 2)})
                else:
                    title = str(item).strip()
                    if title:
                        output.append({"title": title, "priority": 2})
        if output:
            return output
        return fallback or [{"title": "请先人工复核后执行。", "priority": 2}]

    async def _call_json(
        self,
        method: str,
        url: str,
        *,
        payload: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        timeout: float = 12.0,
    ) -> Any | None:
        try:
            async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
                resp = await client.request(method=method, url=url, json=payload, params=params)
            if resp.status_code >= 400:
                return None
            return resp.json()
        except Exception:
            return None

    async def _list_available_beds(self, department_id: str | None = None) -> list[str]:
        dep = (department_id or "").strip() or settings.default_department_id
        rows = await self._call_json("GET", f"{settings.patient_context_service_url}/wards/{dep}/beds", timeout=10)
        beds = self._extract_bed_nos_from_rows(rows)
        if beds:
            return beds

        rows = await self._call_json("GET", f"{settings.patient_context_service_url}/wards/all-beds", timeout=10)
        beds = self._extract_bed_nos_from_rows(rows)
        if beds:
            return beds

        rows = await self._call_json("GET", f"{settings.patient_context_service_url}/wards/_all_beds", timeout=10)
        return self._extract_bed_nos_from_rows(rows)

    async def _write_audit(self, action: str, resource_id: str | None, detail: dict[str, Any], user_id: str | None) -> None:
        await self._call_json(
            "POST",
            f"{settings.audit_service_url}/audit/log",
            payload={
                "user_id": user_id,
                "action": action,
                "resource_type": "workflow",
                "resource_id": resource_id,
                "detail": detail,
            },
            timeout=6,
        )

    async def _fetch_contexts(self, payload: WorkflowRequest, beds: list[str], allow_ward_fallback: bool) -> list[dict[str, Any]]:
        contexts: list[dict[str, Any]] = []
        seen_patient_ids: set[str] = set()
        requested_by = self._normalize_user_id(payload.requested_by)
        available_bed_nos_cache: list[str] | None = None

        async with httpx.AsyncClient(timeout=httpx.Timeout(28, connect=6), trust_env=False) as client:
            async def ensure_available_beds() -> list[str]:
                nonlocal available_bed_nos_cache
                if available_bed_nos_cache is not None:
                    return available_bed_nos_cache

                dep = (payload.department_id or "").strip() or settings.default_department_id
                try:
                    ward_resp = await client.get(f"{settings.patient_context_service_url}/wards/{dep}/beds")
                    ward_rows = ward_resp.json() if ward_resp.status_code < 400 else []
                except Exception:
                    ward_rows = []

                available_bed_nos_cache = self._extract_bed_nos_from_rows(ward_rows)
                if available_bed_nos_cache:
                    return available_bed_nos_cache

                try:
                    all_resp = await client.get(f"{settings.patient_context_service_url}/wards/all-beds")
                    all_rows = all_resp.json() if all_resp.status_code < 400 else []
                except Exception:
                    all_rows = []
                available_bed_nos_cache = self._extract_bed_nos_from_rows(all_rows)
                if available_bed_nos_cache:
                    return available_bed_nos_cache

                try:
                    all_resp = await client.get(f"{settings.patient_context_service_url}/wards/_all_beds")
                    all_rows = all_resp.json() if all_resp.status_code < 400 else []
                except Exception:
                    all_rows = []
                available_bed_nos_cache = self._extract_bed_nos_from_rows(all_rows)
                return available_bed_nos_cache

            for bed in beds:
                requested_bed = str(bed or "").strip()
                resolved_bed = requested_bed
                corrected_bed = False
                params: dict[str, Any] = {}
                if payload.department_id:
                    params["department_id"] = payload.department_id
                params["requested_by"] = requested_by
                try:
                    resp = await client.get(f"{settings.patient_context_service_url}/beds/{resolved_bed}/context", params=params)
                    if resp.status_code >= 400 and payload.department_id:
                        resp = await client.get(
                            f"{settings.patient_context_service_url}/beds/{resolved_bed}/context",
                            params={"requested_by": requested_by},
                        )

                    if resp.status_code >= 400 and requested_bed:
                        available_beds = await ensure_available_beds()
                        nearest_bed = self._resolve_nearest_bed(requested_bed, available_beds)
                        if nearest_bed and nearest_bed != requested_bed:
                            resolved_bed = nearest_bed
                            corrected_bed = True
                            resp = await client.get(
                                f"{settings.patient_context_service_url}/beds/{resolved_bed}/context",
                                params=params,
                            )
                            if resp.status_code >= 400 and payload.department_id:
                                resp = await client.get(
                                    f"{settings.patient_context_service_url}/beds/{resolved_bed}/context",
                                    params={"requested_by": requested_by},
                                )
                    if resp.status_code >= 400:
                        continue
                    body = resp.json()
                except Exception:
                    continue

                if not isinstance(body, dict):
                    continue
                if requested_bed:
                    body["_requested_bed_no"] = requested_bed
                if resolved_bed:
                    body["_resolved_bed_no"] = resolved_bed
                if corrected_bed and requested_bed and resolved_bed:
                    body["_bed_no_corrected"] = True
                    body["_bed_no_correction_note"] = (
                        f"语音床号 {requested_bed} 未命中，已按最近床位 {resolved_bed} 处理。"
                    )
                pid = str(body.get("patient_id") or "").strip()
                if pid and pid in seen_patient_ids:
                    continue
                contexts.append(body)
                if pid:
                    seen_patient_ids.add(pid)

            if not contexts and payload.patient_id:
                try:
                    resp = await client.get(
                        f"{settings.patient_context_service_url}/patients/{payload.patient_id}/context",
                        params={"requested_by": requested_by},
                    )
                    if resp.status_code < 400:
                        body = resp.json()
                        if isinstance(body, dict):
                            contexts.append(body)
                            pid = str(body.get("patient_id") or "").strip()
                            if pid:
                                seen_patient_ids.add(pid)
                except Exception:
                    pass

            if not contexts and allow_ward_fallback and self._is_ward_scope(payload.user_input, beds):
                is_global_scope = self._is_global_scope(payload.user_input)
                dep = (payload.department_id or "").strip() or settings.default_department_id
                if is_global_scope and dep:
                    try:
                        resp = await client.get(f"{settings.patient_context_service_url}/wards/{dep}/beds")
                        ward_beds = resp.json() if resp.status_code < 400 else []
                    except Exception:
                        ward_beds = []
                elif is_global_scope:
                    try:
                        resp = await client.get(f"{settings.patient_context_service_url}/wards/all-beds")
                        if resp.status_code >= 400:
                            resp = await client.get(f"{settings.patient_context_service_url}/wards/_all_beds")
                        ward_beds = resp.json() if resp.status_code < 400 else []
                    except Exception:
                        ward_beds = []
                else:
                    try:
                        resp = await client.get(f"{settings.patient_context_service_url}/wards/{dep}/beds")
                        ward_beds = resp.json() if resp.status_code < 400 else []
                    except Exception:
                        ward_beds = []

                patient_ids: list[str] = []
                if isinstance(ward_beds, list):
                    for bed in ward_beds[:80]:
                        if not isinstance(bed, dict):
                            continue
                        pid = str(bed.get("current_patient_id") or "").strip()
                        if pid and pid not in seen_patient_ids:
                            patient_ids.append(pid)
                            seen_patient_ids.add(pid)

                sem = asyncio.Semaphore(6)

                async def fetch_one(pid: str) -> dict[str, Any] | None:
                    async with sem:
                        try:
                            r = await client.get(
                                f"{settings.patient_context_service_url}/patients/{pid}/context",
                                params={"requested_by": requested_by},
                            )
                            if r.status_code >= 400:
                                return None
                            b = r.json()
                            return b if isinstance(b, dict) else None
                        except Exception:
                            return None

                fetched = await asyncio.gather(*(fetch_one(pid) for pid in patient_ids), return_exceptions=True)
                for item in fetched:
                    if isinstance(item, dict):
                        contexts.append(item)

        return contexts

    async def route_intent(self, text: str) -> WorkflowType:
        raw_q = text or ""
        q = self._strip_prompt_scaffold(raw_q)
        low = q.lower()
        beds = self._extract_beds(raw_q)
        no_patient_clinical_tokens = (
            "生命体征",
            "出入量",
            "病情变化",
            "联系医生",
            "末梢灌注",
            "血压趋势",
            "尿量",
            "氧饱和度",
            "翻身",
            "疼痛复评",
            "家属沟通",
            "压伤",
            "营养",
            "导尿",
            "引流",
            "观察重点",
            "升级处理",
            "交接班",
            "家属",
            "沟通",
            "告知",
            "留痕",
        )
        explicit_document_guidance_tokens = (
            "怎么写",
            "如何写",
            "怎么填",
            "书写要求",
            "填写要求",
            "书写规范",
            "模板",
            "字段",
            "导入",
            "补录",
            "草稿",
            "待补字段",
            "自动回填",
            "提交前校验",
            "提交审核",
            "归档预览",
            "归档入病例",
            "草稿区",
            "工作台",
            "模板正文预览",
            "归档床位",
            "结构化字段",
            "Word",
            "Excel",
        )
        explicit_handover_guidance_tokens = (
            "交班报告",
            "交接班报告",
            "交班草稿",
            "交接班草稿",
            "按什么顺序",
            "书写顺序",
            "重点交代",
            "交班重点",
            "高风险信息",
            "高危信息",
            "交接班模板",
        )
        if self._is_system_design_query(q):
            return WorkflowType.VOICE_INQUIRY
        if self._is_explicit_no_patient_query(q) and not beds:
            if "家属" in q and any(token in q for token in ("沟通", "告知", "留痕")):
                return WorkflowType.RECOMMENDATION
            if any(token in q for token in no_patient_clinical_tokens) and not any(
                token in q for token in (*explicit_document_guidance_tokens, *explicit_handover_guidance_tokens)
            ):
                return WorkflowType.RECOMMENDATION
            if self._is_handover_guidance_query(q):
                return WorkflowType.HANDOVER
            if self._is_document_guidance_query(q):
                return WorkflowType.DOCUMENT
            return WorkflowType.RECOMMENDATION
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
            "血糖记录单",
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
            "手术物品清单",
            "清点记录",
            "中医护理效果评价表",
            "效果评价",
            "辨证施护",
            "健康教育记录",
            "出院宣教",
        )
        inferred_document_type = self._infer_document_type(q)
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
            "编辑",
            "保存草稿",
            "提交审核",
            "归档预览",
            "归档入病例",
            "归档床位",
            "模板正文预览",
            "补充信息",
            "缺失字段",
            "高亮",
            "工作台",
            "Word",
            "Excel",
            "结构化字段",
            "效果评价",
        )
        if inferred_document_type != "nursing_handover_report" and any(
            token in q for token in document_action_tokens
        ):
            return WorkflowType.DOCUMENT
        if any(token in q for token in explicit_document_tokens) and any(
            token in q for token in document_action_tokens
        ):
            return WorkflowType.DOCUMENT
        if any(
            token in q
            for token in (
                "模板正文预览",
                "归档床位",
                "归档预览",
                "提交审核",
                "归档入病例",
                "缺失字段",
                "高亮",
                "Word",
                "Excel",
                "结构化字段",
            )
        ):
            return WorkflowType.DOCUMENT
        if any(token in q for token in ("中医护理效果评价表", "效果评价", "辨证施护", "血糖记录单", "手术物品清单")) and any(
            token in q for token in ("模板", "字段", "补录", "草稿", "审核", "归档", "填写", "怎么写", "怎么填")
        ):
            return WorkflowType.DOCUMENT
        if "体温单" in q and any(token in q for token in ("什么时候", "何时")) and any(
            token in q for token in ("复测", "再评估", "护理记录")
        ):
            return WorkflowType.RECOMMENDATION
        if any(token in q for token in ("导尿管", "导尿", "留置导尿", "堵塞", "尿液混浊", "下腹不适", "引流", "鲜红", "切口情况", "补液平衡", "液体丢失")) and (
            any(token in q for token in bedside_management_tokens)
            or any(token in q for token in ("联系医生", "找医生", "护理记录", "交班", "下一班", "处理什么", "核对什么"))
        ):
            return WorkflowType.RECOMMENDATION
        if any(token in q for token in ("护理日夜交接班报告按什么顺序", "交接班报告按什么顺序", "交班报告按什么顺序")):
            return WorkflowType.HANDOVER
        if any(token in q for token in ("白班护理交接班草稿", "全病区交接班草稿", "生成今天这个病区的白班护理交接班草稿")):
            return WorkflowType.HANDOVER
        if beds and any(token in q for token in ("交班提醒", "一句话交班", "一句话提醒", "比较交班", "对比交班", "分别给一句", "适合交给下一班的提醒", "各一句")) and not any(
            token in q for token in ("输血", "体温单", "血糖", "病重", "病危", "导尿管", "引流", "鲜红", "护理记录")
        ):
            return WorkflowType.HANDOVER
        if any(token in q for token in ("前五个高危重点", "前五个高危", "交代给下一班的前五个高危")):
            return WorkflowType.RECOMMENDATION
        if any(token in q for token in ("谁能等", "谁不能等", "马上处理", "30分钟内处理", "可以稍后处理", "分类依据", "巡查顺序", "谁必须先看")):
            return WorkflowType.RECOMMENDATION
        if self._is_tcm_question(q) and any(token in q for token in ("证候", "饮食", "情志", "护理观察", "转医生", "联系医生")):
            return WorkflowType.RECOMMENDATION
        if self._is_tcm_question(q) and not self._extract_beds(q):
            return WorkflowType.VOICE_INQUIRY
        if any(token in q for token in bedside_management_tokens) and not any(
            token in q for token in ("交班草稿", "交接班草稿", "交班报告", "交接班报告", "白班护理交接班草稿", "全病区交接班草稿")
        ):
            return WorkflowType.RECOMMENDATION
        if self._is_document_guidance_query(q):
            return WorkflowType.DOCUMENT
        if self._is_handover_guidance_query(q) or self._is_explicit_handover_generation(q):
            return WorkflowType.HANDOVER
        if is_autonomous_request(q):
            return WorkflowType.AUTONOMOUS_CARE
        if self._is_doctor_escalation_request(q) or self._is_compare_priority_request(q) or self._is_monitoring_schedule_request(q):
            return WorkflowType.RECOMMENDATION
        if any(t in low for t in self.HANDOVER_TOKENS) or any(t in q for t in ("交班", "交接班")):
            return WorkflowType.HANDOVER
        if any(t in low for t in self.DOCUMENT_TOKENS) or any(t in q for t in ("文书", "草稿", "护理记录")):
            return WorkflowType.DOCUMENT
        if any(t in low for t in self.RECOMMEND_TOKENS) or any(t in q for t in ("建议", "优先级", "风险", "升级")):
            return WorkflowType.RECOMMENDATION
        return WorkflowType.VOICE_INQUIRY

    async def run(self, payload: WorkflowRequest) -> WorkflowOutput:
        payload.requested_by = self._normalize_user_id(payload.requested_by)
        if payload.workflow_type in {
            WorkflowType.AUTONOMOUS_CARE,
            WorkflowType.HANDOVER,
            WorkflowType.RECOMMENDATION,
            WorkflowType.DOCUMENT,
            WorkflowType.VOICE_INQUIRY,
        }:
            workflow_type = await agentic_orchestrator.route_workflow(payload, self.route_intent)
            payload = payload.model_copy(deep=True)
            payload.workflow_type = workflow_type
            memory = agentic_orchestrator.retrieve_memory(payload)
            plan = await agentic_orchestrator.build_plan(payload, workflow_type, memory)
            output = await agentic_orchestrator.run(
                payload,
                helper=self,
                workflow_type=workflow_type,
                memory=memory,
                plan=plan,
                runtime_engine="state_machine",
            )
            if workflow_type == WorkflowType.AUTONOMOUS_CARE:
                critique = agentic_orchestrator.reflect(payload, output)
                if critique.get("followup_actions"):
                    plan = await agentic_orchestrator.build_plan(
                        payload,
                        WorkflowType.AUTONOMOUS_CARE,
                        memory,
                        critique=critique,
                        existing_plan=output.plan,
                    )
                    output = await agentic_orchestrator.run(
                        payload,
                        helper=self,
                        workflow_type=WorkflowType.AUTONOMOUS_CARE,
                        memory=memory,
                        plan=plan,
                        prior_output=output,
                        runtime_engine="state_machine",
                    )
            output = agentic_orchestrator.finalize(payload, output)
            agentic_orchestrator.persist_finalized_run(output)
        else:
            output = await self._run_voice(payload)
        workflow_history_store.append(payload, output)
        return output

    async def preview_scope(
        self,
        payload: WorkflowRequest,
        *,
        allow_ward_fallback: bool = True,
    ) -> PatientScopePreview:
        preview_payload = payload.model_copy(deep=True)
        preview_payload.requested_by = self._normalize_user_id(preview_payload.requested_by)
        question = (preview_payload.user_input or "").strip()
        beds = self._extract_beds(question)
        if preview_payload.bed_no and preview_payload.bed_no not in beds:
            beds.insert(0, preview_payload.bed_no)

        contexts = await self._fetch_contexts(preview_payload, beds, allow_ward_fallback=allow_ward_fallback)
        matched_patients: list[ResolvedScopePatient] = []
        resolved_requested_beds: set[str] = set()

        for context in contexts[:12]:
            if not isinstance(context, dict):
                continue
            requested_bed_no = str(context.get("_requested_bed_no") or "").strip() or None
            resolved_bed_no = str(
                context.get("_resolved_bed_no") or context.get("bed_no") or ""
            ).strip() or None
            correction_note = str(context.get("_bed_no_correction_note") or "").strip() or None
            if requested_bed_no:
                resolved_requested_beds.add(requested_bed_no)
            if resolved_bed_no:
                resolved_requested_beds.add(resolved_bed_no)

            matched_patients.append(
                ResolvedScopePatient(
                    patient_id=str(context.get("patient_id") or "").strip() or None,
                    patient_name=str(
                        context.get("patient_name") or context.get("full_name") or ""
                    ).strip()
                    or None,
                    bed_no=str(context.get("bed_no") or "").strip() or None,
                    diagnoses=[
                        str(item).strip()
                        for item in context.get("diagnoses", [])[:4]
                        if str(item).strip()
                    ],
                    risk_tags=[
                        str(item).strip()
                        for item in context.get("risk_tags", [])[:4]
                        if str(item).strip()
                    ],
                    pending_tasks=[
                        str(item).strip()
                        for item in context.get("pending_tasks", [])[:4]
                        if str(item).strip()
                    ],
                    requested_bed_no=requested_bed_no,
                    resolved_bed_no=resolved_bed_no,
                    bed_no_corrected=bool(context.get("_bed_no_corrected")),
                    correction_note=correction_note,
                )
            )

        unresolved_beds = [
            bed for bed in beds if str(bed or "").strip() and str(bed or "").strip() not in resolved_requested_beds
        ]

        return PatientScopePreview(
            question=question,
            department_id=preview_payload.department_id,
            ward_scope=self._is_ward_scope(question, beds),
            global_scope=self._is_global_scope(question),
            extracted_beds=beds,
            unresolved_beds=unresolved_beds,
            matched_patients=matched_patients,
        )

    def _build_context_findings(self, context: dict[str, Any]) -> list[str]:
        findings: list[str] = []
        for obs in context.get("latest_observations", [])[:5]:
            if not isinstance(obs, dict):
                continue
            name = str(obs.get("name") or "").strip()
            value = str(obs.get("value") or "").strip()
            if name and value:
                findings.append(f"{name}={value}")
        findings.extend([str(tag).strip() for tag in context.get("risk_tags", [])[:4] if str(tag).strip()])
        return findings

    @staticmethod
    def _llm_answer_likely_generic(text: str | None) -> bool:
        t = (text or "").strip()
        if len(t) < 8:
            return True
        markers = (
            "未命中具体患者上下文",
            "补充床号",
            "继续直接提问",
            "云模型未配置",
            "模型调用失败",
            "本地模型当前不可用",
            "请先启动本地中文模型服务",
        )
        return any(marker in t for marker in markers)

    @staticmethod
    def _is_negated_generation_request(question: str | None) -> bool:
        q = AgentStateMachine._strip_prompt_scaffold(question)
        return any(
            token in q
            for token in ("不要生成", "先不要生成", "先不生成", "不需要生成", "不用生成", "暂不生成", "不是要生成")
        )

    @classmethod
    def _is_explicit_handover_generation(cls, question: str | None) -> bool:
        q = cls._strip_prompt_scaffold(question)
        if not q or cls._is_negated_generation_request(q):
            return False
        return any(token in q for token in ("交班", "交接班", "交班报告", "交接班报告")) and any(
            token in q for token in ("生成", "草稿", "起草", "帮我写", "给我写", "写一份", "出一份", "准备一段", "写入")
        )

    @staticmethod
    def _is_compare_priority_request(question: str | None) -> bool:
        q = (question or "").strip()
        if not q:
            return False
        if any(token in q for token in ("交班", "交接班", "交班报告", "交接班报告")) and any(
            token in q for token in ("顺序", "书写要求", "规范", "草稿", "生成", "起草")
        ) and not any(token in q for token in ("前五", "前5", "前三", "前3", "排序", "排优先级", "集中汇报")):
            return False
        compare_tokens = (
            "比较",
            "对比",
            "排序",
            "排优先级",
            "优先",
            "前3",
            "前三",
            "前5",
            "前五",
            "先看谁",
            "少人力",
            "只剩两名护士",
            "巡查顺序",
            "集中汇报",
            "高危重点",
        )
        if any(token in q for token in compare_tokens):
            return True
        has_multi_scope = len(re.findall(r"\d{1,3}\s*床", q)) >= 2 or any(
            token in q for token in ("病区", "全病区", "全部患者", "所有患者", "整体")
        )
        return has_multi_scope and any(
            token in q for token in ("一句话提醒", "一句话交班", "各一句", "前五", "前5", "高危重点")
        )

    @staticmethod
    def _is_doctor_escalation_request(question: str | None) -> bool:
        q = (question or "").strip()
        if not q:
            return False
        explicit_report_tokens = (
            "上报话术",
            "汇报话术",
            "电话上报",
            "给医生",
            "上报医生",
            "医生沟通",
            "医生团队",
            "值班医生",
            "联系值班医生",
            "提醒医生",
            "怎么向医生",
            "怎么给医生说",
            "一句话汇报",
            "一句话上报",
            "电话汇报",
        )
        bedside_or_document_tokens = (
            "床旁",
            "先后顺序",
            "先看什么",
            "先做什么",
            "先处理什么",
            "观察重点",
            "再评估",
            "重新评估",
            "护理记录",
            "交班",
            "下一班",
            "导尿管",
            "引流",
            "腹泻",
            "补液平衡",
            "液体丢失",
            "证候",
            "饮食",
            "情志",
        )
        if any(token in q for token in ("前五", "前5", "高危重点", "下一班")) and any(
            token in q for token in ("交班", "交接班")
        ) and not any(token in q for token in ("汇报", "上报", "电话", "怎么向医生", "怎么给医生说")):
            return False
        if any(token in q for token in bedside_or_document_tokens) and not any(token in q for token in explicit_report_tokens):
            return False
        if any(token in q for token in explicit_report_tokens):
            return True
        if any(token in q for token in ("联系医生", "找医生", "马上找医生", "立即联系医生")) and any(
            token in q for token in ("电话", "汇报", "上报", "一句话", "怎么说", "话术", "集中")
        ):
            return True
        if any(token in q for token in ("汇报", "上报")) and any(
            token in q for token in ("医生", "值班", "集中", "决策", "医嘱")
        ):
            return True
        return ("打电话" in q) and any(token in q for token in ("上报", "汇报", "一句话", "医生"))

    @staticmethod
    def _is_monitoring_schedule_request(question: str | None) -> bool:
        q = (question or "").strip()
        if any(
            token in q
            for token in (
                "床旁",
                "先后顺序",
                "先看什么",
                "先做什么",
                "护理记录",
                "交班",
                "导尿管",
                "引流",
                "补液平衡",
                "证候",
                "饮食",
                "情志",
            )
        ) and not any(
            token in q for token in ("监测计划", "监测表", "时间点清单", "班内监测", "观察节奏", "监测安排", "监测顺序", "观察顺序")
        ):
            return False
        if any(
            token in q
            for token in ("监测计划", "监测表", "时间点清单", "班内监测", "观察节奏", "监测安排", "监测顺序", "观察顺序")
        ):
            return True
        if any(token in q for token in ("30分钟内", "30 分钟内")) and any(token in q for token in ("复核", "复测", "观察")):
            return True
        return ("时间点" in q) and any(token in q for token in ("监测", "观察", "复测", "升级处理"))

    @staticmethod
    def _document_type_label(doc_type: str) -> str:
        mapping = {
            "nursing_note": "一般护理记录单",
            "transfusion_nursing_record": "输血护理记录单",
            "temperature_chart": "体温单",
            "glucose_record": "血糖测量记录单",
            "surgical_count_record": "手术物品清点记录",
            "critical_patient_nursing_record": "病重（病危）患者护理记录",
            "nursing_handover_report": "护理日夜交接班报告",
            "progress_note": "病程记录",
        }
        return mapping.get(doc_type, doc_type)

    @staticmethod
    def _extract_missing_field_labels(structured_fields: Any) -> list[str]:
        if not isinstance(structured_fields, dict):
            return []
        editable_blocks = structured_fields.get("editable_blocks", [])
        if not isinstance(editable_blocks, list):
            return []
        labels: list[str] = []
        for item in editable_blocks:
            if not isinstance(item, dict):
                continue
            if str(item.get("status") or "").strip() != "missing":
                continue
            label = str(item.get("label") or item.get("key") or "").strip()
            if label:
                labels.append(label)
        return labels

    @staticmethod
    def _bed_label(context: dict[str, Any]) -> str:
        bed_no = str(context.get("bed_no") or "-").strip() or "-"
        patient_name = str(context.get("patient_name") or "").strip()
        return f"{bed_no}床（{patient_name}）" if patient_name else f"{bed_no}床"

    @staticmethod
    def _is_handover_guidance_query(question: str | None) -> bool:
        q = AgentStateMachine._strip_prompt_scaffold(question)
        if not q:
            return False
        low = q.lower()
        handover_scope = any(token in q for token in ("交班", "交接班", "交班报告", "交接班报告")) or any(
            token in low for token in ("handover", "shift")
        )
        negated_generation = AgentStateMachine._is_negated_generation_request(q)
        if any(
            token in low
            for token in (
                "generate",
                "draft",
                "create",
                "write",
                "make",
            )
        ):
            if not negated_generation:
                return False
        if any(
            token in q
            for token in (
                "生成",
                "草稿",
                "起草",
                "帮我写",
                "帮我做",
                "给我写",
                "写一份",
                "补一份",
                "出一份",
            )
        ):
            if not negated_generation:
                return False
        if AgentStateMachine._is_doctor_escalation_request(q) or AgentStateMachine._is_compare_priority_request(q) or AgentStateMachine._is_monitoring_schedule_request(q):
            return False
        if not handover_scope:
            return False
        guidance_tokens = (
            "怎么写",
            "如何写",
            "怎么填",
            "怎么做",
            "书写要求",
            "填写要求",
            "规范",
            "格式",
            "顺序",
            "模板",
            "字段",
            "注意事项",
            "按什么顺序",
            "容易漏掉",
            "最容易漏掉",
            "漏项",
            "重点交代",
            "交班重点",
            "高风险信息",
            "高危信息",
        )
        return any(token in q for token in guidance_tokens) or any(
            token in low for token in ("how to", "format", "template", "field", "order", "sequence", "requirement")
        )

    @staticmethod
    def _is_document_guidance_query(question: str | None) -> bool:
        q = AgentStateMachine._strip_prompt_scaffold(question)
        if not q:
            return False
        low = q.lower()
        document_scope = any(
            token in q
            for token in (
                "文书",
                "护理文书",
                "护理记录",
                "记录单",
                "体温单",
                "输血护理记录",
                "血糖测量记录",
                "血糖谱",
                "手术物品清点",
                "清点记录",
                "病危护理记录",
                "病重护理记录",
                "模板",
                "字段",
                "半结构化",
                "补录",
                "漏填",
                "导入",
                "草稿区",
                "提交审核",
                "归档预览",
                "归档入病例",
                "工作台",
                "模板正文预览",
                "归档床位",
                "结构化字段",
                "Word",
                "Excel",
                "txt",
                "docx",
            )
        ) or any(token in low for token in ("document", "draft", "template", "field"))
        if not document_scope:
            return False
        guidance_tokens = (
            "怎么写",
            "如何写",
            "怎么填",
            "书写要求",
            "填写要求",
            "书写规范",
            "格式",
            "模板",
            "字段",
            "导入",
            "半结构化",
            "补录",
            "漏填",
            "缺失",
            "支持哪些",
            "有哪些字段",
            "容易漏掉",
            "最容易漏掉",
            "漏项",
            "草稿区",
            "提交审核",
            "归档预览",
            "归档入病例",
            "工作台",
            "模板正文预览",
            "归档床位",
            "结构化字段",
            "word",
            "excel",
        )
        return any(token in q for token in guidance_tokens) or any(
            token in low for token in ("how to", "format", "template", "field", "import", "missing")
        )

    @staticmethod
    def _build_single_patient_summary(context: dict[str, Any], bed_no: str | None = None) -> str:
        bed = str(bed_no or context.get("bed_no") or "").strip() or "当前"
        patient_name = str(context.get("patient_name") or context.get("full_name") or "").strip()

        segments: list[str] = [f"已定位到{bed}床"]
        if patient_name:
            segments[0] = f"{segments[0]}（{patient_name}）"
        segments[0] = f"{segments[0]}。"

        diagnoses = [str(item).strip() for item in context.get("diagnoses", []) if str(item).strip()]
        if diagnoses:
            segments.append(f"当前诊断：{'、'.join(diagnoses[:3])}。")

        observations: list[str] = []
        for obs in context.get("latest_observations", [])[:6]:
            if not isinstance(obs, dict):
                continue
            name = str(obs.get("name") or "").strip()
            value = str(obs.get("value") or "").strip()
            if not name or not value:
                continue
            abnormal = str(obs.get("abnormal_flag") or "").strip().lower()
            if abnormal and abnormal not in {"normal", "ok", "none"}:
                observations.insert(0, f"{name} {value}（{abnormal}）")
            else:
                observations.append(f"{name} {value}")
        if observations:
            segments.append(f"重点指标：{'；'.join(observations[:3])}。")

        risk_tags = [str(item).strip() for item in context.get("risk_tags", []) if str(item).strip()]
        if risk_tags:
            segments.append(f"风险标签：{'、'.join(risk_tags[:3])}。")

        tasks = [str(item).strip() for item in context.get("pending_tasks", []) if str(item).strip()]
        if tasks:
            segments.append(f"建议先执行：{'、'.join(tasks[:3])}，并人工复核。")
        else:
            segments.append("建议继续监测关键生命体征，按医嘱处理并人工复核。")
        return "".join(segments)

    def _build_escalation_response(
        self,
        context: dict[str, Any],
        question: str,
    ) -> tuple[str, list[str], list[dict[str, Any]]]:
        bed_no = str(context.get("bed_no") or "-").strip() or "-"
        patient_name = str(context.get("patient_name") or "患者").strip() or "患者"
        diagnoses = "、".join([str(item).strip() for item in context.get("diagnoses", [])[:2] if str(item).strip()]) or "当前诊断待核对"
        obs_parts: list[str] = []
        for item in context.get("latest_observations", [])[:3]:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            value = str(item.get("value") or "").strip()
            if name and value:
                obs_parts.append(f"{name}{value}")
        risk_tags = [str(item).strip() for item in context.get("risk_tags", [])[:3] if str(item).strip()]
        pending_tasks = [str(item).strip() for item in context.get("pending_tasks", [])[:3] if str(item).strip()]

        ask_items: list[str] = []
        if any(token in question for token in ("血压", "低血压", "少尿", "尿量")):
            ask_items.append("请明确复测频次、液体/升压策略和尿量目标")
        if any(token in question for token in ("腹痛", "液体丢失", "补液平衡")):
            ask_items.append("请明确补液策略、出入量观察重点和何时需要再次马上找医生")
        if any(token in question for token in ("意识", "GCS", "瞳孔", "再出血", "卒中")):
            ask_items.append("请明确神经系统监测频次、血压控制目标和是否需要进一步评估")
        if any(token in question for token in ("感染", "体温", "恶露", "贫血")):
            ask_items.append("请明确是否补开感染相关检查、复查血常规或进一步处理")
        if not ask_items:
            ask_items.append("请明确下一步医嘱、复测频次和升级处理阈值")

        followup_items: list[str] = []
        if any(token in question for token in ("瞳孔", "意识", "GCS")):
            followup_items.append("瞳孔大小及对光反射、意识变化时间点")
        if any(token in question for token in ("血压", "低血压", "少尿", "尿量")):
            followup_items.append("复测血压、脉搏、近1小时/4小时尿量和总出入量")
        if any(token in question for token in ("腹痛", "液体丢失", "补液平衡")):
            followup_items.append("腹痛评分、腹部体征、出入量、尿量和补液后反应")
        if any(token in question for token in ("恶露", "贫血")):
            followup_items.append("恶露量、颜色、气味及血红蛋白复查结果")
        if any(token in question for token in ("感染", "体温")):
            followup_items.append("最新体温、心率、寒战情况和感染指标")
        if not followup_items:
            followup_items.append("最新生命体征、异常趋势和已执行处置效果")

        immediate_actions = "、".join(pending_tasks[:2]) if pending_tasks else "立即复核生命体征并持续观察"
        record_targets = "；".join(followup_items[:3])
        doctor_trigger = "、".join(risk_tags[:2]) if risk_tags else "异常生命体征持续恶化"
        if any(token in question for token in ("腹痛", "液体丢失", "补液平衡")):
            doctor_trigger = f"{doctor_trigger}、腹痛持续加重、出入量继续失衡或尿量下降"
        spoken = (
            f"第一层（立即处理）：先完成{immediate_actions}。"
            f"第二层（30分钟内复核并记录）：{record_targets}。"
            f"第三层（立即联系医生并上报）：可直接说“医生您好，我汇报{bed_no}床{patient_name}，"
            f"主要情况是{diagnoses}，目前{ '；'.join(obs_parts) if obs_parts else '生命体征存在异常变化' }，"
            f"同时存在{ '、'.join(risk_tags) if risk_tags else '病情波动风险' }。"
            f"我这边已完成{immediate_actions}，现在想请您帮助明确：{ '；'.join(ask_items) }。”"
            f"若30分钟内指标仍未回稳或出现{doctor_trigger}，请立即再次联系医生，必要时马上找医生并补记到护理记录。"
        )

        findings = [
            f"已观察到：{'；'.join(obs_parts) if obs_parts else '请补充最新异常指标'}",
            f"重点风险：{'、'.join(risk_tags) if risk_tags else '请结合当前病情补充风险点'}",
            f"建议先说明已做处理：{immediate_actions}",
            f"30分钟内重点记录：{record_targets}",
            f"若医生追问，优先补充：{'；'.join(followup_items)}",
        ]
        if any(token in question for token in ("腹痛", "液体丢失", "补液平衡")):
            findings.append("腹痛与液体管理场景下，要把出入量、尿量和补液后反应一起交代。")
            findings.append("若腹痛持续加重、出入量继续失衡或尿量下降，要马上找医生。")
        recommendations = [
            {"title": f"先按上述话术上报{bed_no}床，再同步补充客观数据。", "priority": 1},
            {"title": "电话后把医生回复、新医嘱和复测结果立即补记到交班或护理记录。", "priority": 1},
        ]
        if any(token in question for token in ("腹痛", "液体丢失", "补液平衡")):
            recommendations.append({"title": "同步补齐出入量、尿量和补液后反应，异常继续时马上找医生。", "priority": 1})
        return spoken, findings, recommendations

    def _build_monitoring_schedule(
        self,
        contexts: list[dict[str, Any]],
        question: str,
    ) -> tuple[str, list[str], list[dict[str, Any]]]:
        ranked_contexts = sorted(contexts, key=self._risk_score, reverse=True)
        findings: list[str] = []
        recommendations: list[dict[str, Any]] = []
        for context in ranked_contexts[:6]:
            bed_no = str(context.get("bed_no") or "-").strip() or "-"
            obs_names = [
                str(item.get("name") or "").strip()
                for item in context.get("latest_observations", [])[:2]
                if isinstance(item, dict) and str(item.get("name") or "").strip()
            ]
            focus = "、".join(obs_names) or "生命体征"
            risk_tags = [str(item).strip() for item in context.get("risk_tags", [])[:2] if str(item).strip()]
            risk_text = "、".join(risk_tags) or "病情波动"
            findings.extend(
                [
                    f"现在-{bed_no}床-先复核{focus}-若继续异常立即联系医生。",
                    f"30分钟内-{bed_no}床-复查{focus}趋势并补录关键数值-若指标未回稳立即升级处理。",
                    f"本班持续-{bed_no}床-重点盯{risk_text}-出现新发恶化信号立即汇报。",
                ]
            )
            recommendations.append({"title": f"优先先看{bed_no}床，再按时间点推进。", "priority": 1})
        bed_list = "、".join([f"{str(ctx.get('bed_no') or '-') }床" for ctx in ranked_contexts[:4]])
        summary = self._ensure_question(f"已整理班内监测时间点清单，重点覆盖{bed_list}。", question)
        return summary, findings[:18], recommendations[:4]

    async def _dispatch_collaboration(self, payload: WorkflowRequest, source_summary: str) -> str:
        sender = self._normalize_user_id(payload.requested_by)
        patient_id = (payload.patient_id or "").strip() or None

        accounts = await self._call_json(
            "GET",
            f"{settings.collaboration_service_url}/collab/accounts",
            params={"query": "doctor", "exclude_user_id": sender},
            timeout=8,
        )
        if (not isinstance(accounts, list)) or (not accounts):
            accounts = await self._call_json(
                "GET",
                f"{settings.collaboration_service_url}/collab/accounts",
                params={"query": "", "exclude_user_id": sender},
                timeout=8,
            )
            if (not isinstance(accounts, list)) or (not accounts):
                return ""

        target = accounts[0] if isinstance(accounts[0], dict) else {}
        target_user_id = str(target.get("user_id") or target.get("id") or "").strip()
        target_name = str(
            target.get("display_name")
            or target.get("full_name")
            or target.get("username")
            or target.get("account")
            or "值班医生"
        ).strip()
        if not target_user_id:
            return ""

        opened = await self._call_json(
            "POST",
            f"{settings.collaboration_service_url}/collab/direct/open",
            payload={"user_id": sender, "contact_user_id": target_user_id, "patient_id": patient_id},
            timeout=8,
        )
        if not isinstance(opened, dict):
            return ""
        session_id = str(opened.get("id") or opened.get("session_id") or "").strip()
        if not session_id:
            return ""

        sent = await self._call_json(
            "POST",
            f"{settings.collaboration_service_url}/collab/direct/message",
            payload={
                "session_id": session_id,
                "sender_id": sender,
                "content": source_summary[:220],
                "message_type": "text",
                "attachment_refs": [],
            },
            timeout=8,
        )
        if not isinstance(sent, dict):
            return ""
        return f"已发送协作消息给 {target_name}（会话 {session_id[:8]}...）。"

    async def _run_voice(self, payload: WorkflowRequest) -> WorkflowOutput:
        raw_question = (payload.user_input or "").strip()
        question = self._strip_prompt_scaffold(raw_question)
        beds = self._extract_beds(raw_question)
        if payload.bed_no and payload.bed_no not in beds:
            beds.insert(0, payload.bed_no)

        ward_scope = self._is_ward_scope(question, beds)
        explicit_no_patient = self._is_explicit_no_patient_query(question)
        system_design_query = self._is_system_design_query(question)
        allow_ward_fallback = ward_scope and not explicit_no_patient and not system_design_query
        contexts = await self._fetch_contexts(payload, beds, allow_ward_fallback=allow_ward_fallback)

        summary = ""
        findings: list[str] = []
        recommendations: list[dict[str, Any]] = []
        confidence = 0.7

        if system_design_query:
            summary = await self._answer_general_question(question)
            findings = [
                "当前按系统能力与临床落地解释处理，重点说明记忆、连续追踪、今日待办、交接班和人工复核如何衔接。",
            ]
            recommendations = [
                {"title": "如需，我可以继续把机制说明展开成页面模块、流程图或答辩话术。", "priority": 1},
            ]
            confidence = 0.84
        elif not contexts:
            if beds:
                nearest = self._resolve_nearest_bed(beds[0], await self._list_available_beds(payload.department_id))
                if nearest and nearest != beds[0]:
                    summary = self._ensure_question(
                        f"未找到 {beds[0]} 床患者上下文。你可能在问 {nearest} 床，我可以按 {nearest} 床继续处理。",
                        question,
                    )
                    recommendations = [
                        {"title": f"直接说“查看{nearest}床情况”继续", "priority": 1},
                        {"title": "也可以改为“查看病区高风险患者”", "priority": 2},
                    ]
                else:
                    summary = self._ensure_question(f"未找到 {beds[0]} 床患者上下文，请确认床号或科室后重试。", question)
                    recommendations = [
                        {"title": "确认床号后重试", "priority": 1},
                        {"title": "也可以直接说“查看病区高风险患者”", "priority": 2},
                    ]
                confidence = 0.42
            else:
                if self._is_tcm_question(question):
                    summary = await self._answer_tcm_question(question, None)
                    findings = ["当前未绑定具体病例，本次按中医护理通用问答处理。"]
                    recommendations = [
                        {"title": "继续直接问中医护理观察、辨证线索或转医生阈值即可。", "priority": 1},
                        {"title": "如果要细化到某位患者，我可以继续进入对应患者档案展开。", "priority": 2},
                    ]
                    confidence = 0.76
                else:
                    summary = await self._answer_general_question(question)
                    findings = ["当前未绑定具体病例，本次按通用问答处理。"]
                    recommendations = [
                        {"title": "继续直接提问即可，不需要先选病例。", "priority": 1},
                        {"title": "如果要结合某位患者，再补充床号或病区。", "priority": 2},
                    ]
                    confidence = 0.74
        elif ward_scope or len(contexts) > 1:
            ranked = sorted(
                [
                    {
                        "patient_id": str(ctx.get("patient_id") or ""),
                        "bed_no": str(ctx.get("bed_no") or "-"),
                        "risk_score": self._risk_score(ctx),
                        "risk_tags": len(ctx.get("risk_tags") or []),
                        "pending": len(ctx.get("pending_tasks") or []),
                    }
                    for ctx in contexts
                ],
                key=lambda item: item["risk_score"],
                reverse=True,
            )

            if settings.voice_llm_enabled:
                ranking_text = "\n".join(
                    [f"床位{row['bed_no']}: score={row['risk_score']} risk={row['risk_tags']} pending={row['pending']}" for row in ranked[:15]]
                )
                llm_answer = await bailian_refine(
                    "你是护理值班调度助手。请根据以下病区风险排序，输出："
                    "1) 总结 2) 前三优先动作 3) 上报条件。\n"
                    f"{ranking_text}\n用户问题：{question}"
                )
                if self._llm_unavailable(llm_answer):
                    top3 = "、".join([f"{row['bed_no']}床(分值{row['risk_score']})" for row in ranked[:3]])
                    llm_answer = f"已完成病区风险排序。当前建议优先处理：{top3}。"
                summary = self._ensure_question(llm_answer, question)
            else:
                top3 = "、".join([f"{row['bed_no']}床(分值{row['risk_score']})" for row in ranked[:3]])
                summary = self._ensure_question(f"已完成病区风险排序。当前建议优先处理：{top3}。", question)
            if any(token in question for token in ("前五", "前5", "下一班", "高危重点")):
                top5 = "、".join([f"{row['bed_no']}床" for row in ranked[:5]])
                summary = self._ensure_question(f"已整理病区今班前五个高危交班重点：{top5}，下一班需继续重点盯防并按阈值联系医生。", question)
            findings = [f"{row['bed_no']}床：风险分={row['risk_score']}（风险标签{row['risk_tags']}，待办{row['pending']}）" for row in ranked[:8]]
            recommendations = [{"title": f"优先处理 {row['bed_no']}床", "priority": 1} for row in ranked[:4]]
            confidence = 0.85
        else:
            context = contexts[0]
            payload.patient_id = str(context.get("patient_id") or payload.patient_id or "")
            payload.bed_no = str(context.get("bed_no") or payload.bed_no or "")
            if self._is_tcm_question(question) or any(token in question for token in ("中医", "中西医结合", "证候")):
                summary = await self._answer_tcm_question(question, context)
                recommendations = [
                    {"title": "重点观察气短、夜间喘憋与氧饱和度变化。", "priority": 1},
                    {"title": "连续记录浮肿范围、尿量和体重变化。", "priority": 1},
                    {"title": "若血压继续下降、呼吸困难加重或少尿，立即联系医生。", "priority": 1},
                ]
            else:
                deterministic_summary = self._build_single_patient_summary(context, payload.bed_no)
                summary = self._ensure_question(deterministic_summary, question)
            findings = self._build_context_findings(context)
            correction_note = str(context.get("_bed_no_correction_note") or "").strip()
            requested_bed = str(context.get("_requested_bed_no") or "").strip()
            resolved_bed = str(context.get("_resolved_bed_no") or payload.bed_no or "").strip()
            if not correction_note and requested_bed and resolved_bed and requested_bed != resolved_bed:
                correction_note = f"语音床号 {requested_bed} 已纠偏为 {resolved_bed}。"
            if correction_note:
                summary = f"{correction_note}{summary}"
                findings = [correction_note, *findings]
                confidence = 0.79
            if not recommendations:
                recommendations = [
                    {"title": f"优先处理：{str(task).strip()}", "priority": 1}
                    for task in context.get("pending_tasks", [])[:4]
                    if str(task).strip()
                ]
            if not recommendations:
                recommendations = [{"title": "继续监测关键指标并按医嘱复核。", "priority": 2}]
            confidence = max(confidence, 0.8)

        steps = [
            AgentStep(agent="Intent Router Agent", status="done"),
            AgentStep(agent="Patient Context Agent", status="done" if contexts else "skipped"),
        ]

        if any(token in question for token in self.COLLAB_TOKENS):
            note = await self._dispatch_collaboration(payload, summary)
            if note:
                recommendations.insert(0, {"title": note, "priority": 1})
                steps.append(AgentStep(agent="Collaboration Agent", status="done"))

        steps.extend(
            [
                AgentStep(agent="Reasoning Agent", status="done", output={"confidence": confidence}),
                AgentStep(agent="Audit Agent", status="done"),
            ]
        )

        await self._write_audit(
            action="workflow.voice_inquiry",
            resource_id=str(payload.patient_id or ""),
            detail={
                "question": question,
                "bed_no": payload.bed_no,
                "context_count": len(contexts),
                "ward_scope": ward_scope,
            },
            user_id=payload.requested_by,
        )

        resolved_patient_id = str(payload.patient_id or "").strip() or None
        resolved_bed_no = str(payload.bed_no or "").strip() or None
        resolved_patient_name: str | None = None
        if contexts and isinstance(contexts[0], dict):
            first_ctx = contexts[0]
            if not resolved_patient_id:
                resolved_patient_id = str(first_ctx.get("patient_id") or "").strip() or None
            if not resolved_bed_no:
                resolved_bed_no = str(first_ctx.get("bed_no") or "").strip() or None
            resolved_patient_name = str(first_ctx.get("patient_name") or "").strip() or None

        return WorkflowOutput(
            workflow_type=WorkflowType.VOICE_INQUIRY,
            summary=summary,
            findings=findings,
            recommendations=self._normalize_recommendations(recommendations),
            confidence=confidence,
            review_required=True,
            context_hit=bool(contexts),
            patient_id=resolved_patient_id,
            patient_name=resolved_patient_name,
            bed_no=resolved_bed_no,
            steps=steps,
            created_at=datetime.now(timezone.utc),
        )

    async def _run_handover(self, payload: WorkflowRequest) -> WorkflowOutput:
        raw_question = (payload.user_input or "").strip()
        question = self._strip_prompt_scaffold(raw_question)
        guidance_query = self._is_handover_guidance_query(question)
        beds = self._extract_beds(raw_question)
        if payload.bed_no and payload.bed_no not in beds:
            beds.insert(0, payload.bed_no)
        ward_scope = self._is_ward_scope(question, beds)
        explicit_no_patient = self._is_explicit_no_patient_query(question)
        contexts = await self._fetch_contexts(payload, beds, allow_ward_fallback=ward_scope and not guidance_query)

        steps = [
            AgentStep(agent="Intent Router Agent", status="done"),
            AgentStep(agent="Patient Context Agent", status="done" if contexts else "skipped"),
            AgentStep(agent="Handover Agent", status="done"),
            AgentStep(agent="Audit Agent", status="done"),
        ]

        if explicit_no_patient and not beds and "家属" in question and any(token in question for token in ("沟通", "告知", "留痕")):
            guidance = await self._answer_general_question(question)
            return WorkflowOutput(
                workflow_type=WorkflowType.RECOMMENDATION,
                summary=self._ensure_question(guidance, question),
                findings=[
                    "当前未绑定具体病例，本次按通用家属沟通场景处理。",
                    "重点覆盖沟通框架、需要同步告知医生的升级信号、留痕要点和交接班提醒口径。",
                ],
                recommendations=[
                    {"title": "可继续追问某一类场景，如低血压、低氧、输血反应或术后恢复的沟通话术。", "priority": 1},
                    {"title": "如果后续要落到具体患者，再补充床号或患者档案即可自动衔接文书与交接班。", "priority": 2},
                ],
                confidence=0.88,
                review_required=True,
                steps=steps,
                created_at=datetime.now(timezone.utc),
            )

        if guidance_query and any(token in question for token in ("顺序", "先后", "书写顺序", "书写要求", "正式书写")) and (
            not any(token in question for token in ("外出请假", "请假", "去向", "返区"))
            or any(token in question for token in ("出科", "入科", "病重", "病危", "高危患者", "当日手术", "次日手术"))
        ):
            return WorkflowOutput(
                workflow_type=WorkflowType.HANDOVER,
                summary=self._ensure_question(
                    "护理日夜交接班报告一般按“出科（出院、转出、死亡）→ 入科（入院、转入）→ 病重/病危 → 当日手术 → 病情变化 → 次日手术/特殊治疗检查 → 高危患者 → 外出请假 → 其他特殊情况”的顺序书写。",
                    question,
                ),
                findings=[
                    "眉栏需交代住院总数、出院、入院、手术、病危、病重、抢救、死亡等统计信息。",
                    "病重/病危、手术、病情变化和高危患者要重点交班。",
                    "外出请假患者需写明去向、请假时间、医生意见和告知内容。",
                ],
                recommendations=[
                    {"title": "如需，我可以继续展开某一类患者的交班字段、重点和常见漏项。", "priority": 1},
                    {"title": "白班按黑色笔、夜班按红色笔的电子化规范展示。", "priority": 2},
                ],
                confidence=0.86,
                review_required=True,
                steps=steps,
                created_at=datetime.now(timezone.utc),
            )

        if guidance_query and any(token in question for token in ("外出请假", "请假", "去向", "医生意见", "告知内容")):
            return WorkflowOutput(
                workflow_type=WorkflowType.HANDOVER,
                summary=self._ensure_question(
                    "外出请假患者在护理日夜交接班报告里，至少要写明去向、请假时间、医生意见、告知内容，以及返区（回病区）后的再评估和后续观察安排。",
                    question,
                ),
                findings=[
                    "请假信息至少包括去向、外出起止时间、是否已获医生同意和家属联系方式。",
                    "告知内容要写清外出期间风险提示、按时返区要求、异常情况处理方式和返区后需立即复评。",
                    "患者返区后要补记生命体征、症状变化、外出期间特殊情况，以及是否需要联系医生继续处理。",
                ],
                recommendations=[
                    {"title": "交班时按“去向与时间-医生意见-告知内容-返区后复评”四段式交代。", "priority": 1},
                    {"title": "如返区后生命体征异常、症状加重或未按时返区，应立即联系医生并补记交班。", "priority": 1},
                ],
                confidence=0.86,
                review_required=True,
                steps=steps,
                created_at=datetime.now(timezone.utc),
            )

        if guidance_query and any(token in question for token in ("容易漏掉", "最容易漏掉", "漏项", "高风险信息", "高危信息", "重点交代", "交班重点")):
            return WorkflowOutput(
                workflow_type=WorkflowType.HANDOVER,
                summary=self._ensure_question(
                    "交接班最容易漏掉的高风险信息通常有三类：病情突变与异常生命体征、重点治疗和时效性处置、以及安全风险与未完成闭环事项。",
                    question,
                ),
                findings=[
                    "病情突变与异常生命体征：如低血压、低氧、发热、意识改变、疼痛骤增、出血或引流异常。",
                    "重点治疗与时效任务：如输血、术后观察、即将到期医嘱、待复测指标、待执行检查和需尽快沟通医生的事项。",
                    "安全风险与未闭环事项：如跌倒、压伤、非计划性拔管、外出请假、情绪行为异常，以及本班已发现但下一班还需继续跟进的内容。",
                ],
                recommendations=[
                    {"title": "交班时按“异常变化-已做处理-下一班要盯什么”三句式交代。", "priority": 1},
                    {"title": "高风险患者要明确具体床号、时间点和未完成事项，避免只说笼统风险。", "priority": 1},
                ],
                confidence=0.84,
                review_required=True,
                steps=steps,
                created_at=datetime.now(timezone.utc),
            )

        if not contexts:
            if ward_scope or any(token in question for token in ("全病区", "病区", "今日待办", "交接摘要", "交班摘要", "晨会", "夜班", "护士长")):
                return WorkflowOutput(
                    workflow_type=WorkflowType.HANDOVER,
                    summary=self._ensure_question(
                        "已按病区交接视角整理输出框架：先列今日待办，再列高风险患者、当日手术与病情变化、未完成闭环事项，最后补次日手术和外出请假患者。",
                        question,
                    ),
                    findings=[
                        "今日待办建议固定包含：高风险复评、时效性医嘱、待补文书、待联系医生事项和下一班继续观察点。",
                        "交接班摘要要优先点名病危/病重、术后返回、异常生命体征、输血/血糖/引流等时效任务。",
                        "若当前尚未锁定具体患者，也可以先生成病区摘要骨架，后续再逐床补入客观数据和责任护士签名。",
                    ],
                    recommendations=[
                        {"title": "可直接继续下达：生成今日病区交接摘要、今日待办和高风险患者交班提醒。", "priority": 1},
                        {"title": "后续如补充具体床号，我会把交班内容自动落到对应患者条目。", "priority": 1},
                    ],
                    confidence=0.84,
                    review_required=True,
                    steps=steps,
                    created_at=datetime.now(timezone.utc),
                )
            if any(token in question for token in ("外出请假", "请假", "返区", "去向")):
                return WorkflowOutput(
                    workflow_type=WorkflowType.HANDOVER,
                    summary=self._ensure_question(
                        "外出请假患者在交接班报告里至少要写明去向、请假时间、医生意见、告知内容，以及返区后的再评估安排。",
                        question,
                    ),
                    findings=[
                        "去向与请假信息：写清外出去向、起止时间、是否已获医生同意和陪同情况。",
                        "返区后评估：返区后要补记生命体征、症状变化、意识状态和外出期间是否发生特殊情况。",
                        "必要时联系医生：若返区后评估异常、症状加重或未按时返区，应立即联系医生并补记交班。",
                    ],
                    recommendations=[
                        {"title": "交班时按“去向-时间-医生意见-返区后评估”四段式整理。", "priority": 1},
                        {"title": "把返区后的评估结果同步写入护理记录和交班。", "priority": 1},
                    ],
                    confidence=0.84,
                    review_required=True,
                    steps=steps,
                    created_at=datetime.now(timezone.utc),
                )
            if guidance_query:
                return WorkflowOutput(
                    workflow_type=WorkflowType.HANDOVER,
                    summary=self._ensure_question(
                        "护理日夜交接班报告一般按“出科（出院、转出、死亡）→ 入科（入院、转入）→ 病重/病危 → 当日手术 → 病情变化 → 次日手术/特殊治疗检查 → 高危患者 → 外出请假 → 其他特殊情况”的顺序书写。",
                        question,
                    ),
                    findings=[
                        "眉栏需交代住院总数、出院、入院、手术、病危、病重、抢救、死亡等统计信息。",
                        "病重/病危、手术、病情变化和高危患者要重点交班。",
                        "外出请假患者需写明去向、请假时间、医生意见和告知内容。",
                    ],
                    recommendations=[
                        {"title": "如需，我可以继续展开某一类患者的交班字段、重点和常见漏项。", "priority": 1},
                        {"title": "白班按黑色笔、夜班按红色笔的电子化规范展示。", "priority": 2},
                    ],
                    confidence=0.82,
                    review_required=True,
                    steps=steps,
                    created_at=datetime.now(timezone.utc),
                )
            return WorkflowOutput(
                workflow_type=WorkflowType.HANDOVER,
                summary=self._ensure_question("未命中患者上下文。请补充床号，或直接说“生成全病区交班草稿”。", question),
                findings=[],
                recommendations=[{"title": "示例：请生成23床交班草稿。", "priority": 1}],
                confidence=0.3,
                review_required=True,
                steps=steps,
                created_at=datetime.now(timezone.utc),
            )

        if ward_scope or len(contexts) > 1:
            if any(
                token in question
                for token in ("顺序", "规范顺序", "交接班报告", "交接班摘要", "今日待办", "高危患者", "异常事件")
            ) and any(
                token in question
                for token in ("出科", "入科", "病重", "病危", "当日手术", "次日手术", "外出请假", "白班")
            ):
                ranked_contexts = sorted(contexts, key=self._risk_score, reverse=True)
                focus_beds = "、".join([f"{str(ctx.get('bed_no') or '-') }\u5e8a" for ctx in ranked_contexts[:4]]) or "重点床位"
                return WorkflowOutput(
                    workflow_type=WorkflowType.HANDOVER,
                    summary=self._ensure_question(
                        "今日护理交接班建议按固定顺序同步协作模块今日待办：出科 → 入科 → 病重病危 → 当日手术 → 病情变化 → 次日手术/特殊检查 → 高危患者 → 外出请假 → 异常事件。"
                        f"当前可先围绕 {focus_beds} 这些重点床位起草白班摘要。",
                        question,
                    ),
                    findings=[
                        "出科：先交代本班出院、转出、死亡患者的去向、时间和是否已完成离科前核对。",
                        "入科：写清新入院、转入患者的诊断、首班评估、风险分层和入科后首要观察重点。",
                        "病重病危与当日手术：优先点名当前风险最高患者，说明已做处理、再评估时间和是否需继续联系医生。",
                        "次日手术/特殊检查：把禁食、备血、术前评估、待完成医嘱和特殊检查准备写进今日待办同步区。",
                        f"高危患者与异常事件：重点覆盖 {focus_beds} 的低血压、低氧、跌倒、输血及其他未闭环事项，并明确下一班继续观察点。",
                    ],
                    recommendations=[
                        {"title": "可直接在草稿区生成一段交接班摘要：先列出科/入科，再列病重病危、当日手术、高危患者和异常事件。", "priority": 1},
                        {"title": "把协作模块里的今日待办逐条映射到对应交接班栏目，避免待办口径和交班口径不一致。", "priority": 1},
                    ],
                    confidence=0.88,
                    review_required=True,
                    context_hit=True,
                    steps=steps,
                    created_at=datetime.now(timezone.utc),
                )
            compare_mode = any(token in question for token in ("比较", "对比", "各自", "分别", "一句话提醒", "一句话交班", "各一句"))
            if compare_mode:
                ranked_contexts = sorted(contexts, key=self._risk_score, reverse=True)
                findings = []
                recommendations = []
                for context in ranked_contexts[:6]:
                    bed_no = str(context.get("bed_no") or "-").strip() or "-"
                    patient_name = str(context.get("patient_name") or "").strip()
                    reason = self._context_priority_reason(context)
                    label = f"{bed_no}床"
                    if patient_name:
                        label = f"{label}（{patient_name}）"
                    findings.append(f"{label}：{reason}")
                    reminder_parts: list[str] = []
                    risk_tags = [str(item).strip() for item in context.get("risk_tags", [])[:2] if str(item).strip()]
                    pending_tasks = [str(item).strip() for item in context.get("pending_tasks", [])[:2] if str(item).strip()]
                    if risk_tags:
                        reminder_parts.append("、".join(risk_tags))
                    if pending_tasks:
                        reminder_parts.append(f"下一班继续{pending_tasks[0]}")
                    reminder = "；".join(reminder_parts) or "继续复核生命体征和未完成处置"
                    recommendations.append({"title": f"{label}交班提醒：{reminder}", "priority": 1})
                compared_beds = "、".join([f"{str(ctx.get('bed_no') or '-') }床" for ctx in ranked_contexts[:4]])
                top_bed = f"{str(ranked_contexts[0].get('bed_no') or '-') }床" if ranked_contexts else "当前首位患者"
                return WorkflowOutput(
                    workflow_type=WorkflowType.HANDOVER,
                    summary=self._ensure_question(f"已比较{compared_beds}的交班重点，当前最该先提醒的是{top_bed}。", question),
                    findings=findings,
                    recommendations=recommendations,
                    confidence=0.86,
                    review_required=True,
                    context_hit=True,
                    steps=steps,
                    created_at=datetime.now(timezone.utc),
                )
            dep = payload.department_id or settings.default_department_id
            batch = await self._call_json(
                "POST",
                f"{settings.handover_service_url}/handover/batch-generate",
                payload={"department_id": dep, "generated_by": payload.requested_by},
                timeout=24,
            )
            if isinstance(batch, list) and batch:
                ranked_contexts = sorted(contexts, key=self._risk_score, reverse=True)
                focus_beds = "、".join([f"{str(ctx.get('bed_no') or '-') }床" for ctx in ranked_contexts[:4]])
                summary = self._ensure_question(
                    f"已生成病区交接班草稿，共 {len(batch)} 份，重点覆盖 {focus_beds} 等高危床位，请先审核后提交。",
                    question,
                )
                if "白班" in question and "白班" not in summary:
                    summary = f"本次为白班交接班草稿。{summary}"
                if any(token in question for token in ("顺序", "固定顺序", "正式顺序")) and "顺序" not in summary:
                    summary = f"{summary} 内容已按规范顺序组织。"
                if any(token in question for token in ("观察", "次日仍需观察", "次日")) and "观察" not in summary:
                    summary = f"{summary} 已补充次日仍需观察的重点。"
                findings = []
                for item in batch[:8]:
                    if not isinstance(item, dict):
                        continue
                    record_summary = str(item.get("summary") or "").strip()
                    match = re.search(r"(\d{1,3})床", record_summary)
                    if match:
                        findings.append(f"已生成：{match.group(1)}床交班草稿")
                    else:
                        findings.append(f"已生成患者：{str(item.get('patient_id', 'unknown'))}")
                if any(token in question for token in ("高危", "高风险")):
                    findings.append("高危患者：草稿已优先覆盖风险最高床位，待护士逐项审核。")
                if any(token in question for token in ("观察", "次日")):
                    findings.append("观察重点：已补充次日仍需观察的生命体征、症状变化和未闭环事项。")
                recommendations = [{"title": "先审核高风险患者交班草稿。", "priority": 1}]
                confidence = 0.82
            else:
                ranked_contexts = sorted(contexts, key=self._risk_score, reverse=True)
                focus_beds = "、".join([f"{str(ctx.get('bed_no') or '-') }床" for ctx in ranked_contexts[:4]]) or "高风险床位"
                summary = self._ensure_question(
                    "批量交班服务暂不可用，但已按规范顺序整理病区交接班摘要框架：出科→入科→病重/病危→当日手术→病情变化→次日手术/特殊检查→高危患者→外出请假→异常事件。"
                    f"当前建议先审核 {focus_beds} 的重点条目，再补正式草稿。",
                    question,
                )
                findings = [
                    "出科：先核对本班出院、转出、死亡患者的去向与时间。",
                    "入科：补齐新入院、转入患者的诊断、评估与首班观察重点。",
                    "病重/病危与当日手术：优先点名高风险患者并写清已做处理。",
                    "高危患者与异常事件：明确低血压、低氧、输血、跌倒和未闭环事项，以及下一班继续观察点。",
                ]
                recommendations = [
                    {"title": "先人工复核高风险患者条目，再生成正式交接班草稿。", "priority": 1},
                    {"title": "把今日待办、医生沟通结果和未闭环事项同步写入交接班摘要。", "priority": 1},
                ]
                confidence = 0.72
        else:
            context = contexts[0]
            patient_id = str(context.get("patient_id") or payload.patient_id or "").strip()
            record = await self._call_json(
                "POST",
                f"{settings.handover_service_url}/handover/generate",
                payload={"patient_id": patient_id, "generated_by": payload.requested_by},
                timeout=18,
            )
            if isinstance(record, dict):
                record_id = str(record.get("id") or "").strip()
                summary = str(record.get("summary") or "交班草稿已生成。").strip()
                if record_id:
                    summary = f"{summary}（交班ID: {record_id}）"
                summary = f"{self._bed_label(context)}交接班草稿已生成。{summary}"
                if "联系医生" not in summary:
                    summary = f"{summary} 如出现新发恶化或关键指标继续异常，请立即联系医生。"
                summary = self._ensure_question(summary, question)
                findings = [str(item).strip() for item in record.get("worsening_points", [])[:5] if str(item).strip()]
                findings.extend(self._build_context_findings(context))
                findings.extend([str(item).strip() for item in context.get("pending_tasks", [])[:4] if str(item).strip()])
                recommendations = [
                    {"title": f"下一班优先：{str(item).strip()}", "priority": 1}
                    for item in record.get("next_shift_priorities", [])[:4]
                    if str(item).strip()
                ]
                recommendations.append({"title": "如恶化信号继续出现，立即联系医生并同步更新交班记录。", "priority": 1})
                confidence = 0.84
            else:
                summary = self._ensure_question("交班服务暂不可用，请稍后重试。", question)
                findings = []
                recommendations = [{"title": "稍后重试交班生成。", "priority": 1}]
                confidence = 0.45

        await self._write_audit(
            action="workflow.handover",
            resource_id=str(payload.patient_id or ""),
            detail={"question": question, "bed_no": payload.bed_no},
            user_id=payload.requested_by,
        )
        resolved_patient_id = str(payload.patient_id or "").strip() or None
        resolved_bed_no = str(payload.bed_no or "").strip() or None
        resolved_patient_name: str | None = None
        if contexts and isinstance(contexts[0], dict):
            first_ctx = contexts[0]
            if not resolved_patient_id:
                resolved_patient_id = str(first_ctx.get("patient_id") or "").strip() or None
            if not resolved_bed_no:
                resolved_bed_no = str(first_ctx.get("bed_no") or "").strip() or None
            resolved_patient_name = str(first_ctx.get("patient_name") or "").strip() or None
        return WorkflowOutput(
            workflow_type=WorkflowType.HANDOVER,
            summary=summary,
            findings=findings,
            recommendations=self._normalize_recommendations(recommendations),
            confidence=confidence,
            review_required=True,
            context_hit=bool(contexts),
            patient_id=resolved_patient_id,
            patient_name=resolved_patient_name,
            bed_no=resolved_bed_no,
            steps=steps,
            created_at=datetime.now(timezone.utc),
        )

    async def _run_recommendation(self, payload: WorkflowRequest) -> WorkflowOutput:
        raw_question = (payload.user_input or "").strip()
        question = self._strip_prompt_scaffold(raw_question)
        beds = self._extract_beds(raw_question)
        if payload.bed_no and payload.bed_no not in beds:
            beds.insert(0, payload.bed_no)
        ward_scope = self._is_ward_scope(question, beds)
        explicit_no_patient = self._is_explicit_no_patient_query(question)
        multi_scope = len(beds) > 1 or any(
            token in question for token in ("全病区", "整个病区", "病区里", "全部患者", "所有患者", "前五", "前5", "多床", "多个床", "分别")
        )
        contexts = await self._fetch_contexts(payload, beds, allow_ward_fallback=not explicit_no_patient and (ward_scope or multi_scope))

        steps = [
            AgentStep(agent="Intent Router Agent", status="done"),
            AgentStep(agent="Patient Context Agent", status="done" if contexts else "skipped"),
            AgentStep(agent="Recommendation Agent", status="done"),
            AgentStep(agent="Audit Agent", status="done"),
        ]

        if not contexts:
            if explicit_no_patient:
                guidance = await self._answer_general_question(question)
                return WorkflowOutput(
                    workflow_type=WorkflowType.RECOMMENDATION,
                    summary=self._ensure_question(guidance, question),
                    findings=["当前未绑定具体病例，本次按通用护理问题处理。"],
                    recommendations=[
                        {"title": "继续直接追问观察重点、复评时点、联系医生阈值或交接班表述即可。", "priority": 1},
                        {"title": "如果后续要落到具体患者，再补充床号或患者档案。", "priority": 2},
                    ],
                    confidence=0.82,
                    review_required=True,
                    steps=steps,
                    created_at=datetime.now(timezone.utc),
                )
            if ward_scope or any(token in question for token in ("全病区", "病区", "多床", "前五", "巡查顺序", "优先级", "今日待办", "谁最急")):
                return WorkflowOutput(
                    workflow_type=WorkflowType.RECOMMENDATION,
                    summary=self._ensure_question(
                        "已按病区工作流给出建议框架：先按风险排序床位，再区分马上处理、30分钟内处理、可稍后处理，并同步整理今日待办和交接提醒。",
                        question,
                    ),
                    findings=[
                        "病区级建议的核心不是泛泛回答，而是给出巡查顺序、联系医生阈值、需补文书项目和下一班继续观察点。",
                        "高风险患者通常优先看呼吸循环、意识、出入量/引流、输血/血糖节点和未闭环医嘱。",
                        "若暂时没有锁定具体床号，也可以先让 AI 生成病区待办与交班骨架，后续再逐床补实。 ",
                    ],
                    recommendations=[
                        {"title": "可继续下达：按病区排优先级、生成今日待办、比较前五高风险床位。", "priority": 1},
                        {"title": "若补充具体床号或患者，建议会自动收敛到床旁执行层。", "priority": 1},
                    ],
                    confidence=0.82,
                    review_required=True,
                    steps=steps,
                    created_at=datetime.now(timezone.utc),
                )
            if "体温单" in question and any(token in question for token in ("复测", "降温", "再评估", "护理记录")):
                return WorkflowOutput(
                    workflow_type=WorkflowType.RECOMMENDATION,
                    summary=self._ensure_question("发热患者体温单补录时，应先明确何时复测、何时降温后再评估，以及何时把异常经过补入护理记录。", question),
                    findings=[
                        "复测时间：体温≥37.5℃一般按每4小时复测1次；病情变化、寒战或高热时应提前复测。",
                        "降温后再评估：采取物理或药物降温后，要在达到处理后观察时点再次评估体温变化，并用红圈、虚线等规范标记。",
                        "护理记录：若高热反复波动、降温后仍不退或体温单版面不足，应把降温措施、复测结果和病情变化补入护理记录。",
                    ],
                    recommendations=[
                        {"title": "先补测量时间点，再补复测值和降温后再评估结果。", "priority": 1},
                        {"title": "发热持续不退或伴生命体征恶化时，立即联系医生。", "priority": 1},
                        {"title": "把异常体温经过同步写入护理记录和交班。", "priority": 1},
                    ],
                    confidence=0.84,
                    review_required=True,
                    steps=steps,
                    created_at=datetime.now(timezone.utc),
                )
            if any(token in question for token in ("腹泻", "液体丢失", "补液平衡", "脱水")):
                return WorkflowOutput(
                    workflow_type=WorkflowType.RECOMMENDATION,
                    summary=self._ensure_question("腹泻伴液体丢失时，即使未指定床号，也应先盯出入量、尿量和腹痛变化，再决定何时联系医生。", question),
                    findings=[
                        "床旁观察重点：先看意识、口干、皮肤弹性、腹痛和心率变化，再核对腹泻次数、补液执行情况和当前尿量。",
                        "出入量与尿量：按班次汇总出入量，重点盯尿量、腹泻量和补液后反应；若尿量继续减少或腹泻量明显增加，应及时再评估循环状态。",
                        "联系医生阈值：若腹痛加重、尿量进一步下降、血压波动或补液后仍有明显脱水表现，应立即联系医生。",
                    ],
                    recommendations=[
                        {"title": "先把出入量、尿量和腹泻次数补入护理记录。", "priority": 1},
                        {"title": "补液后按计划重新评估生命体征、尿量和腹痛。", "priority": 1},
                        {"title": "交班时明确下一班继续盯出入量、尿量和再次联系医生阈值。", "priority": 1},
                    ],
                    confidence=0.84,
                    review_required=True,
                    steps=steps,
                    created_at=datetime.now(timezone.utc),
                )
            if any(token in question for token in ("导尿管", "导尿", "留置导尿", "尿液混浊", "下腹不适", "堵塞")):
                return WorkflowOutput(
                    workflow_type=WorkflowType.RECOMMENDATION,
                    summary=self._ensure_question("导尿患者疑似堵塞或感染时，即使暂时没指定床号，也应先按床旁顺序看病人、再看导尿管和尿量变化，并及时补齐护理记录。", question),
                    findings=[
                        "床旁先后顺序：先看患者下腹胀痛、生命体征和不适程度，再检查导尿管是否受压、扭曲、牵拉或引流袋位置不当，随后复核尿量和尿液颜色/混浊度，并判断是否存在感染征象。",
                        "联系医生阈值：若导尿管复位后仍无尿、尿量持续明显减少、伴发热寒战、下腹胀痛加重、肉眼血尿或感染表现加重，应立即联系医生。",
                        "护理记录重点：写清导尿管状态、尿量变化、尿液颜色/混浊度、感染线索、已做处理、联系医生时间和后续观察计划。",
                    ],
                    recommendations=[
                        {"title": "先人工确认导尿管通畅、引流袋位置和当前尿量。", "priority": 1},
                        {"title": "出现持续无尿、明显血尿或感染征象时立即联系医生。", "priority": 1},
                        {"title": "把导尿管异常经过和护理记录同步补齐，便于下一班追踪。", "priority": 1},
                    ],
                    confidence=0.84,
                    review_required=True,
                    steps=steps,
                    created_at=datetime.now(timezone.utc),
                )
            if any(token in question for token in ("引流", "鲜红", "切口", "引流液")):
                return WorkflowOutput(
                    workflow_type=WorkflowType.RECOMMENDATION,
                    summary=self._ensure_question("术后引流异常时，没有指定床号也应先核对引流量和颜色；鲜红且较前增多时立即联系医生，并把交班重点写明。", question),
                    findings=[
                        "床旁观察顺序：先看患者面色、血压、心率和切口渗血，再核对引流装置位置、引流是否通畅、当前引流量与颜色，重点留意鲜红引流液是否较前增加。",
                        "联系医生阈值：若引流液持续鲜红、短时间内量明显增加、伴血压下降、心率增快、切口渗血加重或患者头晕乏力，应立即联系医生。",
                        "交班重点：写清引流量、颜色、切口情况、已做复核和联系医生情况，并交代下一班继续盯引流变化和生命体征。",
                    ],
                    recommendations=[
                        {"title": "先补记当前引流量、鲜红程度和切口观察结果。", "priority": 1},
                        {"title": "达到鲜红增多或循环不稳阈值时立即联系医生。", "priority": 1},
                        {"title": "把引流异常经过写入交班和护理记录，便于下一班持续追踪。", "priority": 1},
                    ],
                    confidence=0.84,
                    review_required=True,
                    steps=steps,
                    created_at=datetime.now(timezone.utc),
                )
            return WorkflowOutput(
                workflow_type=WorkflowType.RECOMMENDATION,
                summary=self._ensure_question(
                    "未命中患者上下文。建议先补充床号；若是病区问题，可直接说“按病区排优先级”。",
                    question,
                ),
                findings=[],
                recommendations=[{"title": "补充床号后可输出更精准建议。", "priority": 1}],
                confidence=0.58,
                review_required=True,
                steps=steps,
                created_at=datetime.now(timezone.utc),
            )

        if multi_scope or ward_scope:
            if any(token in question for token in ("今日待办", "待办", "交接重点", "交班重点", "交接班重点")):
                ranked_contexts = sorted(contexts, key=self._risk_score, reverse=True)
                top_beds = "、".join([self._bed_label(ctx) for ctx in ranked_contexts[:5]])
                findings = []
                recommendations = []
                for idx, context in enumerate(ranked_contexts[:5], start=1):
                    label = self._bed_label(context)
                    context_findings = self._build_context_findings(context)
                    next_shift_focus = context_findings[0] if context_findings else "继续复核生命体征和待办闭环"
                    findings.append(
                        f"优先{idx}：{label}；当前原因={self._context_priority_reason(context)}；交接重点={next_shift_focus}。"
                    )
                    recommendations.append(
                        {
                            "title": f"{label}：本班先处理 {next_shift_focus}",
                            "priority": 1 if idx <= 2 else 2,
                        }
                    )
                return WorkflowOutput(
                    workflow_type=WorkflowType.RECOMMENDATION,
                    summary=self._ensure_question(f"已按病区整理今日待办和交接重点：当前优先关注 {top_beds}。", question),
                    findings=findings,
                    recommendations=recommendations,
                    confidence=0.87,
                    review_required=True,
                    steps=steps,
                    created_at=datetime.now(timezone.utc),
                )
            if any(token in question for token in ("巡查顺序", "谁必须先看", "谁可以后看", "危险信号", "只剩两名护士", "少人力")):
                ranked_contexts = sorted(contexts, key=self._risk_score, reverse=True)
                findings = []
                recommendations = []
                for idx, context in enumerate(ranked_contexts[:5], start=1):
                    label = self._bed_label(context)
                    context_findings = self._build_context_findings(context)
                    first_signal = context_findings[0] if context_findings else "继续复核生命体征"
                    findings.append(f"第{idx}位先看{label}：第一眼抓{first_signal}，同时留意危险信号和{self._context_priority_reason(context)}。")
                    recommendations.append({"title": f"巡查时先看{label}，发现危险信号立即联系医生。", "priority": 1})
                ordered_beds = "、".join([self._bed_label(ctx) for ctx in ranked_contexts[:5]])
                return WorkflowOutput(
                    workflow_type=WorkflowType.RECOMMENDATION,
                    summary=self._ensure_question(f"已整理病区巡查顺序：{ordered_beds}。请先看前两位高危患者，其余按风险继续巡查，并重点盯住危险信号。", question),
                    findings=findings,
                    recommendations=recommendations,
                    confidence=0.86,
                    review_required=True,
                    steps=steps,
                    created_at=datetime.now(timezone.utc),
                )
            if any(token in question for token in ("谁能等", "谁不能等", "马上处理", "30分钟内处理", "可以稍后处理", "分类依据")):
                ranked_contexts = sorted(contexts, key=self._risk_score, reverse=True)
                immediate = [self._bed_label(ctx) for ctx in ranked_contexts[:2]]
                soon = [self._bed_label(ctx) for ctx in ranked_contexts[2:5]]
                later = [self._bed_label(ctx) for ctx in ranked_contexts[5:8]]
                findings = [
                    f"必须马上处理：{'、'.join(immediate) or '暂无'}；分类依据是当前异常指标最重、需要优先联系医生或立即复核。",
                    f"30分钟内处理：{'、'.join(soon) or '暂无'}；分类依据是存在明确风险，但可在完成前两位处置后迅速跟进。",
                    f"可以稍后处理：{'、'.join(later) or '暂无'}；分类依据是当前指标相对稳定，但仍需本班持续观察和交班留意。",
                ]
                recommendations = [
                    {"title": f"马上处理：{'、'.join(immediate) or '暂无'}。", "priority": 1},
                    {"title": f"30分钟内处理：{'、'.join(soon) or '暂无'}。", "priority": 1},
                    {"title": f"可以稍后处理：{'、'.join(later) or '暂无'}。", "priority": 2},
                ]
                return WorkflowOutput(
                    workflow_type=WorkflowType.RECOMMENDATION,
                    summary=self._ensure_question("已按“马上处理、30分钟内处理、可以稍后处理”三层完成病区分类，并写明分类依据。", question),
                    findings=findings,
                    recommendations=recommendations,
                    confidence=0.86,
                    review_required=True,
                    steps=steps,
                    created_at=datetime.now(timezone.utc),
                )
            if any(token in question for token in ("比较", "对比", "谁最急", "最急", "为什么最急")):
                ranked_contexts = sorted(contexts, key=self._risk_score, reverse=True)
                findings = []
                recommendations = []
                for idx, context in enumerate(ranked_contexts[:5], start=1):
                    label = self._bed_label(context)
                    context_findings = self._build_context_findings(context)
                    focus = context_findings[0] if context_findings else "继续复核生命体征"
                    urgency = "当前最急" if idx == 1 else f"第{idx}位关注"
                    findings.append(f"{label}：{urgency}；原因={self._context_priority_reason(context)}；下一班重点={focus}。")
                    recommendations.append({"title": f"{label}：下一班继续盯{focus}。", "priority": 1 if idx == 1 else 2})
                most_urgent = self._bed_label(ranked_contexts[0]) if ranked_contexts else "当前首位患者"
                return WorkflowOutput(
                    workflow_type=WorkflowType.RECOMMENDATION,
                    summary=self._ensure_question(f"已完成多床风险比较，当前最急的是{most_urgent}，并已说明为什么最急和下一班最该盯什么。", question),
                    findings=findings,
                    recommendations=recommendations,
                    confidence=0.86,
                    review_required=True,
                    steps=steps,
                    created_at=datetime.now(timezone.utc),
                )
            if self._is_tcm_question(question):
                ranked_contexts = sorted(contexts, key=self._risk_score, reverse=True)
                focus_contexts = ranked_contexts[:2] or contexts[:2]
                findings = []
                for idx, context in enumerate(focus_contexts, start=1):
                    label = self._bed_label(context)
                    if idx == 1:
                        findings.append(
                            f"{label}：证候线索偏气血不足或脾虚，饮食宜温软清淡、少量多餐；情志重点观察乏力、睡眠浅和焦虑变化，若头晕加重、纳差明显或血压继续下降，应立即联系医生。"
                        )
                    else:
                        findings.append(
                            f"{label}：证候线索偏痰热壅肺夹气阴受损，饮食宜温润易消化并减少辛辣刺激；情志重点观察烦躁、焦虑和夜间不宁，若咳喘加重、痰色转黄稠或血氧继续下降，应立即联系医生。"
                        )
                recommendations = [
                    {"title": "先按证候线索补充护理观察和饮食调护，再把情志变化写入交班。", "priority": 1},
                    {"title": "出现呼吸、循环或神志恶化时立即联系医生，不要只停留在中医观察层面。", "priority": 1},
                ]
                return WorkflowOutput(
                    workflow_type=WorkflowType.RECOMMENDATION,
                    summary=self._ensure_question("已按多床情境整理中医护理要点，重点补齐证候、饮食、情志和联系医生阈值，便于直接用于护理观察与交班。", question),
                    findings=findings,
                    recommendations=recommendations,
                    confidence=0.86,
                    review_required=True,
                    steps=steps,
                    created_at=datetime.now(timezone.utc),
                )
            if self._is_doctor_escalation_request(question):
                ranked = sorted(contexts, key=self._risk_score, reverse=True)
                findings = []
                recommendations = []
                for context in ranked[:6]:
                    label = self._bed_label(context)
                    obs = self._build_context_findings(context)
                    key_obs = "；".join(obs[:2]) or "异常生命体征待复核"
                    pending = [str(item).strip() for item in context.get("pending_tasks", [])[:2] if str(item).strip()]
                    findings.append(
                        f"{label}：当前最危险点={self._context_priority_reason(context)}；已完成/待完成={ '、'.join(pending) if pending else '持续观察与复测' }；仍需医生决策={key_obs}"
                    )
                    recommendations.append({"title": f"集中汇报时先报{label}，再补充关键客观指标。", "priority": 1})
                compared_beds = "、".join([f"{str(ctx.get('bed_no') or '-') }床" for ctx in ranked[:4]])
                return WorkflowOutput(
                    workflow_type=WorkflowType.RECOMMENDATION,
                    summary=self._ensure_question(f"已整理{compared_beds}的值班医生集中汇报摘要，并已按风险高低完成排序。", question),
                    findings=findings,
                    recommendations=recommendations,
                    confidence=0.86,
                    review_required=True,
                    steps=steps,
                    created_at=datetime.now(timezone.utc),
                )
            if self._is_monitoring_schedule_request(question):
                summary, findings, recommendations = self._build_monitoring_schedule(contexts, question)
                return WorkflowOutput(
                    workflow_type=WorkflowType.RECOMMENDATION,
                    summary=summary,
                    findings=findings,
                    recommendations=self._normalize_recommendations(recommendations),
                    confidence=0.86,
                    review_required=True,
                    steps=steps,
                    created_at=datetime.now(timezone.utc),
                )
            if any(token in question for token in ("前五", "前5", "下一班", "高危重点", "交给下一班", "交代给下一班")):
                ranked_contexts = sorted(contexts, key=self._risk_score, reverse=True)
                findings = []
                recommendations = []
                for context in ranked_contexts[:5]:
                    label = self._bed_label(context)
                    context_findings = self._build_context_findings(context)
                    next_shift_focus = context_findings[0] if context_findings else "继续复核生命体征"
                    findings.append(
                        f"{label}：危险原因={self._context_priority_reason(context)}；下一班重点={next_shift_focus}；达到恶化阈值请立即联系医生。"
                    )
                    recommendations.append({"title": f"下一班先盯{label}，出现新发恶化立即联系医生。", "priority": 1})
                top_five = "、".join([f"{str(ctx.get('bed_no') or '-') }床" for ctx in ranked_contexts[:5]])
                return WorkflowOutput(
                    workflow_type=WorkflowType.RECOMMENDATION,
                    summary=self._ensure_question(f"已整理病区今班前五个高危交班重点：{top_five}。", question),
                    findings=findings,
                    recommendations=recommendations,
                    confidence=0.86,
                    review_required=True,
                    steps=steps,
                    created_at=datetime.now(timezone.utc),
                )
            ranked = sorted(
                [
                    {
                        "bed_no": str(ctx.get("bed_no") or "-"),
                        "risk_score": self._risk_score(ctx),
                        "reason": self._context_priority_reason(ctx),
                    }
                    for ctx in contexts
                ],
                key=lambda x: x["risk_score"],
                reverse=True,
            )
            top_beds = "、".join([f"{row['bed_no']}床" for row in ranked[:3]])
            return WorkflowOutput(
                workflow_type=WorkflowType.RECOMMENDATION,
                summary=self._ensure_question(f"已完成病区风险排序，当前前三位优先处理对象为 {top_beds}。", question),
                findings=[
                    f"{row['bed_no']}床：风险分={row['risk_score']}；{row['reason']}"
                    for row in ranked[:8]
                ],
                recommendations=[
                    {"title": f"优先处理 {row['bed_no']}床", "priority": 1, "rationale": row["reason"]}
                    for row in ranked[:5]
                ],
                confidence=0.84,
                review_required=True,
                steps=steps,
                created_at=datetime.now(timezone.utc),
            )

        context = contexts[0]
        patient_id = str(context.get("patient_id") or payload.patient_id or "").strip()
        bed_no = str(context.get("bed_no") or payload.bed_no or "")
        bed_label = self._bed_label(context)

        if any(token in question for token in ("低血压", "少尿", "收缩压", "四肢偏凉", "末梢")) and any(
            token in question for token in ("床旁", "再评估", "护理记录", "交班", "联系医生")
        ):
            return WorkflowOutput(
                workflow_type=WorkflowType.RECOMMENDATION,
                summary=self._ensure_question(f"{bed_label}需先完成床旁复核，再按再评估和重新评估结果决定是否立即联系医生，并把护理记录和交班同步补齐。", question),
                findings=[
                    "床旁先后顺序：先看意识、皮肤温度和末梢灌注，再复测血压/心率/尿量，随后核对补液通路、最近出入量和夜班已执行处理。",
                    "再评估时间点：完成初步处理后建议15-30分钟内再评估血压、尿量、末梢灌注和头晕是否缓解；必要时做重新评估。若收缩压仍<90 mmHg或尿量继续下降，应立即联系医生。",
                    "护理记录重点：写清低血压少尿表现、已做床旁处置、再评估结果、联系医生时间与反馈；交班重点写下一班继续盯血压、尿量、意识和补液后反应。",
                ],
                recommendations=[
                    {"title": "先补完整出入量和尿量趋势，再完成再评估和重新评估。", "priority": 1},
                    {"title": "达到收缩压持续低于90 mmHg、尿量继续下降或末梢灌注变差时，立即联系医生。", "priority": 1},
                    {"title": "把床旁复核、再评估和医生沟通同步写进护理记录并交班。", "priority": 1},
                ],
                confidence=0.88,
                review_required=True,
                context_hit=True,
                patient_id=patient_id or None,
                patient_name=str(context.get("patient_name") or "").strip() or None,
                bed_no=bed_no or None,
                steps=steps,
                created_at=datetime.now(timezone.utc),
            )

        if any(token in question for token in ("输血", "输血前", "输血前评估", "血液输注")) and any(
            token in question for token in ("评估", "记录", "汇报", "医生")
        ):
            return WorkflowOutput(
                workflow_type=WorkflowType.RECOMMENDATION,
                summary=self._ensure_question(f"{bed_label}准备输血前，应先完成输血前评估、补齐输血相关记录，再决定哪些异常需要先汇报医生。", question),
                findings=[
                    "输血前评估：先核对血型、输血史、既往输血反应史、当前生命体征、发热寒战情况和感染相关表现。",
                    "记录补写：先补输血前评估、双人核对准备、输血前生命体征及输血开始前待完成栏位，保证后续输血护理记录连续。",
                    "联系医生阈值：若存在发热加重、寒战、生命体征不稳、疑似活动性感染未评估清楚或血红蛋白下降伴症状，应先汇报医生再决定是否启动输血。",
                ],
                recommendations=[
                    {"title": "先完成输血前评估和关键记录，再执行双人核对。", "priority": 1},
                    {"title": "评估中发现感染或循环不稳时，先联系医生明确输血方案。", "priority": 1},
                    {"title": "把评估结果、已补记录和医生反馈同步写入护理记录及交班。", "priority": 1},
                ],
                confidence=0.87,
                review_required=True,
                context_hit=True,
                patient_id=patient_id or None,
                patient_name=str(context.get("patient_name") or "").strip() or None,
                bed_no=bed_no or None,
                steps=steps,
                created_at=datetime.now(timezone.utc),
            )

        if any(token in question for token in ("跌倒", "下床", "烦躁", "安全")):
            return WorkflowOutput(
                workflow_type=WorkflowType.RECOMMENDATION,
                summary=self._ensure_question(f"{bed_label}夜间烦躁伴跌倒高风险时，本班要先抓安全动作，再持续观察情绪、步态和再次下床冲动。", question),
                findings=[
                    "安全动作优先：先把床栏、呼叫铃、夜间照明、陪护协助和如厕看护落实到位，必要时床旁陪护，避免患者自行下床。",
                    "观察重点：盯烦躁程度、定向力、步态稳定性、镇静/止痛后反应以及是否再次强烈要求下床。",
                    "升级条件：若烦躁明显加重、出现谵妄、反复强行下床或已有跌倒前兆，应立即联系医生并同步加强安全看护。",
                ],
                recommendations=[
                    {"title": "先落实跌倒高风险安全动作，再安排专人观察。", "priority": 1},
                    {"title": "把烦躁表现、安全措施和医生沟通补入护理记录与交班。", "priority": 1},
                    {"title": "下一班继续盯下床冲动、步态和环境安全。", "priority": 1},
                ],
                confidence=0.87,
                review_required=True,
                context_hit=True,
                patient_id=patient_id or None,
                patient_name=str(context.get("patient_name") or "").strip() or None,
                bed_no=bed_no or None,
                steps=steps,
                created_at=datetime.now(timezone.utc),
            )

        if any(token in question for token in ("胸闷", "胸痛", "胸部压榨痛", "胸骨后疼痛")):
            if self._is_tcm_question(question):
                return WorkflowOutput(
                    workflow_type=WorkflowType.RECOMMENDATION,
                    summary=self._ensure_question(f"{bed_label}胸闷胸痛先按急症风险处理，中医护理只能作为辅助观察和调护，不能替代医生评估。", question),
                    findings=[
                        "先处理什么：立即停止活动，协助患者取相对舒适体位，马上复测血压、脉搏、呼吸、血氧，并观察疼痛部位、性质、持续时间和是否放射。",
                        "中医护理观察：可辅助关注胸痹相关证候线索，如气滞血瘀、痰浊痹阻、气虚血瘀或阳虚水泛；同时留意舌象、脉象、寒热、情志和痰湿表现。",
                        "联系医生阈值：若胸痛持续不缓解，或伴呼吸困难、血压下降、出冷汗、恶心呕吐、意识变化或血氧下降，应立即联系医生。",
                    ],
                    recommendations=[
                        {"title": "先完成生命体征与胸痛特征复核，再补中医护理观察记录。", "priority": 1},
                        {"title": "把胸痛开始时间、诱因、伴随症状和医生沟通时间写入护理记录与交班。", "priority": 1},
                        {"title": "异常持续或加重时立即联系医生，不要只做中医调护观察。", "priority": 1},
                    ],
                    confidence=0.89,
                    review_required=True,
                    context_hit=True,
                    patient_id=patient_id or None,
                    patient_name=str(context.get("patient_name") or "").strip() or None,
                    bed_no=bed_no or None,
                    steps=steps,
                    created_at=datetime.now(timezone.utc),
                )
            return WorkflowOutput(
                workflow_type=WorkflowType.RECOMMENDATION,
                summary=self._ensure_question(f"{bed_label}胸闷胸痛属于高风险异常，先完成床旁复核并尽快联系医生，不要只停留在一般疼痛处理。", question),
                findings=[
                    "床旁先后顺序：先让患者停止活动并取相对舒适体位，再复测血压、脉搏、呼吸、血氧，询问疼痛部位、性质、持续时间和是否放射。",
                    "重点危险信号：胸痛持续不缓解、伴呼吸困难、血压下降、出冷汗、恶心呕吐、面色苍白、意识变化或血氧下降。",
                    "记录留痕：要写清异常开始时间、生命体征变化、已做处理、医生沟通时间和下一次复评时间点。",
                ],
                recommendations=[
                    {"title": "先完成生命体征和胸痛特征复核，再持续床旁观察。", "priority": 1},
                    {"title": "一旦胸痛持续不缓解或伴循环/呼吸恶化，立即联系医生。", "priority": 1},
                    {"title": "把本班处置、医生反馈和下一班继续观察点同步写入护理记录与交班。", "priority": 1},
                ],
                confidence=0.9,
                review_required=True,
                context_hit=True,
                patient_id=patient_id or None,
                patient_name=str(context.get("patient_name") or "").strip() or None,
                bed_no=bed_no or None,
                steps=steps,
                created_at=datetime.now(timezone.utc),
            )

        if any(token in question for token in ("疼痛", "疼痛评分", "复评", "镇痛")):
            return WorkflowOutput(
                workflow_type=WorkflowType.RECOMMENDATION,
                summary=self._ensure_question(f"{bed_label}术后疼痛评分升高时，应先处理疼痛并观察切口，再按时完成复评和联系医生判断。", question),
                findings=[
                    "先处理什么：先看疼痛评分、切口渗血渗液、体温和心率，再核对已用镇痛措施和切口感染风险表现。",
                    "复评时间：胃肠外给药后通常15-30分钟复评，口服镇痛药后一般1-2小时复评；若疼痛评分仍高或切口红肿热痛加重，要提前复评。",
                    "联系医生阈值：若疼痛持续不缓解、疼痛评分明显升高、切口渗液增多、伴发热或感染迹象加重，应立即联系医生。",
                ],
                recommendations=[
                    {"title": "先完成镇痛处理和切口观察，再按时复评。", "priority": 1},
                    {"title": "把疼痛变化、复评时间和切口情况写入护理记录及交班。", "priority": 1},
                    {"title": "如复评后疼痛仍高或切口感染风险上升，立即联系医生。", "priority": 1},
                ],
                confidence=0.87,
                review_required=True,
                context_hit=True,
                patient_id=patient_id or None,
                patient_name=str(context.get("patient_name") or "").strip() or None,
                bed_no=bed_no or None,
                steps=steps,
                created_at=datetime.now(timezone.utc),
            )

        if any(token in question for token in ("腹泻", "液体丢失", "补液平衡", "口干", "脱水")):
            return WorkflowOutput(
                workflow_type=WorkflowType.RECOMMENDATION,
                summary=self._ensure_question(f"{bed_label}要把补液平衡、出入量、尿量变化和下一班交代一次说清，避免只停留在“继续观察”。", question),
                findings=[
                    "床旁观察重点：先看意识、皮肤弹性、口干程度、腹痛和心率变化，再核对近班腹泻次数、补液执行情况和当前尿量。",
                    "补液平衡与出入量：按班次汇总出入量，重点盯尿量、腹泻量和补液后反应；若尿量继续减少、心率增快或口渴乏力加重，应及时再评估循环状态。",
                    "联系医生与下一班：若腹痛加重、尿量进一步下降、血压波动或补液后仍脱水表现明显，应立即联系医生；下一班需继续追踪出入量、尿量和腹泻频次。",
                ],
                recommendations=[
                    {"title": "先把出入量、尿量和腹泻次数补入护理记录。", "priority": 1},
                    {"title": "补液后按计划再评估生命体征和尿量变化。", "priority": 1},
                    {"title": "交班时明确下一班继续盯出入量、尿量和再次联系医生阈值。", "priority": 1},
                ],
                confidence=0.87,
                review_required=True,
                context_hit=True,
                patient_id=patient_id or None,
                patient_name=str(context.get("patient_name") or "").strip() or None,
                bed_no=bed_no or None,
                steps=steps,
                created_at=datetime.now(timezone.utc),
            )

        if any(token in question for token in ("导尿管", "留置导尿", "尿液混浊", "下腹不适", "堵塞")):
            return WorkflowOutput(
                workflow_type=WorkflowType.RECOMMENDATION,
                summary=self._ensure_question("留置导尿异常时要先看病人、再看导尿管通畅和尿量变化，并把护理记录及时补齐。", question),
                findings=[
                    "床旁先后顺序：先看患者下腹胀痛、生命体征和不适程度，再检查导尿管是否受压、扭曲、牵拉或引流袋位置不当，随后复核尿量和尿液颜色/混浊度。",
                    "联系医生阈值：若导尿管复位后仍无尿、尿量持续明显减少、伴发热寒战、下腹胀痛加重或肉眼血尿，应立即联系医生。",
                    "护理记录重点：写清导尿管状态、尿量变化、尿液颜色/混浊度、已做处理、联系医生时间和后续观察计划。",
                ],
                recommendations=[
                    {"title": "先人工确认导尿管通畅、引流袋位置和当前尿量。", "priority": 1},
                    {"title": "出现持续无尿、明显血尿或感染征象时立即联系医生。", "priority": 1},
                    {"title": "把导尿管异常经过和护理记录同步补齐，便于下一班追踪。", "priority": 1},
                ],
                confidence=0.87,
                review_required=True,
                context_hit=bool(bed_no or patient_id),
                patient_id=patient_id or None,
                patient_name=str(context.get("patient_name") or "").strip() or None,
                bed_no=bed_no or None,
                steps=steps,
                created_at=datetime.now(timezone.utc),
            )

        if any(token in question for token in ("引流", "鲜红", "引流液")):
            return WorkflowOutput(
                workflow_type=WorkflowType.RECOMMENDATION,
                summary=self._ensure_question("术后引流异常应先核对引流量和颜色，鲜红且较前增多时立即联系医生，并把交班重点写明。", question),
                findings=[
                    "床旁观察顺序：先看患者面色、血压、心率和切口渗血，再核对引流装置位置、引流是否通畅、当前引流量与颜色，重点留意鲜红引流液是否较前增加。",
                    "联系医生阈值：若引流液持续鲜红、短时间内量明显增加、伴血压下降、心率增快、切口渗血加重或患者头晕乏力，应立即联系医生。",
                    "交班重点：写清引流量、颜色、切口情况、已做复核和联系医生情况，并交代下一班继续盯引流变化和生命体征。",
                ],
                recommendations=[
                    {"title": "先补记当前引流量、鲜红程度和切口观察结果。", "priority": 1},
                    {"title": "达到鲜红增多或循环不稳阈值时立即联系医生。", "priority": 1},
                    {"title": "把引流异常经过写入交班和护理记录，便于下一班持续追踪。", "priority": 1},
                ],
                confidence=0.88,
                review_required=True,
                context_hit=bool(bed_no or patient_id),
                patient_id=patient_id or None,
                patient_name=str(context.get("patient_name") or "").strip() or None,
                bed_no=bed_no or None,
                steps=steps,
                created_at=datetime.now(timezone.utc),
            )

        if self._is_tcm_question(question):
            tcm_summary = await self._answer_tcm_question(question, context)
            tcm_findings = self._build_context_findings(context)
            if "证候" not in tcm_summary:
                tcm_summary = f"{tcm_summary} 同时要结合舌象、脉象、口渴、纳差、二便和睡眠变化，持续辨别证候走向。"
            if "饮食" in question and "饮食" not in tcm_summary:
                tcm_summary = f"{tcm_summary} 饮食护理上宜结合当前证候做清淡、易消化或温润调护，避免生冷、油腻和明显加重症状的食物。"
            if "情志" in question and "情志" not in tcm_summary:
                tcm_summary = f"{tcm_summary} 情志护理上要观察焦虑、烦躁、失眠和配合度变化，必要时先安抚解释并及时反馈医生。"
            if "联系医生" not in tcm_summary:
                tcm_summary = f"{tcm_summary} 如出现血压继续下降、尿量减少、呼吸困难加重或意识变化，应立即联系医生。"
            return WorkflowOutput(
                workflow_type=WorkflowType.RECOMMENDATION,
                summary=self._ensure_question(tcm_summary, question),
                findings=tcm_findings,
                recommendations=[
                    {"title": f"先按{bed_no or '-'}床中西医结合护理重点持续观察。", "priority": 1},
                    {"title": "异常趋势出现时立即联系医生，并同步补记护理记录。", "priority": 1},
                ],
                confidence=0.86,
                review_required=True,
                context_hit=True,
                patient_id=patient_id or None,
                patient_name=str(context.get('patient_name') or '').strip() or None,
                bed_no=bed_no or None,
                steps=steps,
                created_at=datetime.now(timezone.utc),
            )

        if self._is_doctor_escalation_request(question):
            summary, findings, recommendations = self._build_escalation_response(context, question)
            return WorkflowOutput(
                workflow_type=WorkflowType.RECOMMENDATION,
                summary=self._ensure_question(summary, question),
                findings=findings,
                recommendations=self._normalize_recommendations(recommendations),
                confidence=0.87,
                review_required=True,
                context_hit=True,
                patient_id=patient_id or None,
                patient_name=str(context.get("patient_name") or "").strip() or None,
                bed_no=bed_no or None,
                steps=steps,
                created_at=datetime.now(timezone.utc),
            )

        rec = await self._call_json(
            "POST",
            f"{settings.recommendation_service_url}/recommendation/run",
            payload={
                "patient_id": patient_id,
                "question": question or f"请给出 {bed_no} 床风险处理建议",
                "bed_no": bed_no or None,
                "department_id": payload.department_id,
                "attachments": payload.attachments,
                "requested_by": payload.requested_by,
                "fast_mode": True,
            },
            timeout=32,
        )

        if isinstance(rec, dict):
            summary = self._ensure_question(str(rec.get("summary") or ""), question)
            findings = [str(item).strip() for item in rec.get("findings", []) if str(item).strip()]
            recommendations = self._normalize_recommendations(rec.get("recommendations"))
            confidence = float(rec.get("confidence", 0.8) or 0.8)
        else:
            summary = self._ensure_question("推荐服务暂不可用，已回退为基础风险提示。", question)
            findings = []
            recommendations = [
                {"title": f"优先处理：{str(task).strip()}", "priority": 1}
                for task in context.get("pending_tasks", [])[:4]
                if str(task).strip()
            ] or [{"title": "先复核生命体征并通知医生。", "priority": 1}]
            confidence = 0.72

        if self._is_monitoring_schedule_request(question):
            schedule_summary, schedule_findings, schedule_recommendations = self._build_monitoring_schedule([context], question)
            summary = schedule_summary
            findings = schedule_findings
            recommendations = self._normalize_recommendations(schedule_recommendations)

        if any(token in question for token in ("联系医生", "找医生", "马上找医生", "何时联系医生", "什么时候联系医生")):
            if "联系医生" not in summary and "找医生" not in summary:
                summary = f"{summary} 如出现升级阈值，请立即联系医生。"
            if not any(("联系医生" in item) or ("找医生" in item) for item in findings):
                findings.append("达到升级阈值时应立即联系医生。")
            if not any(
                ("联系医生" in str(item.get("title") or "")) or ("找医生" in str(item.get("title") or ""))
                for item in recommendations
                if isinstance(item, dict)
            ):
                recommendations.append({"title": "若指标继续恶化，请马上找医生并补记护理记录。", "priority": 1})
        if any(token in question for token in ("出入量", "尿量")) and not any(token in "\n".join([summary, *findings]) for token in ("出入量", "尿量")):
            findings.append("请同步复核出入量、尿量变化和最近班次累计记录。")
            recommendations.append({"title": "把出入量和尿量趋势补入护理记录及交班。", "priority": 1})
        if "找医生" in question and not any("找医生" in part for part in [summary, *findings]):
            findings.append("如腹痛、生命体征或液体丢失风险继续加重，请马上找医生。")
        if "下一班" in question and not any("下一班" in part for part in [summary, *findings]):
            findings.append("下一班需继续盯关键指标变化、复核结果和未完成处置。")
        if any(token in question for token in ("腹痛", "液体丢失", "补液平衡")):
            if not any(token in "\n".join([summary, *findings]) for token in ("出入量", "尿量")):
                findings.append("请同步记录出入量、尿量和补液前后反应。")
                recommendations.append({"title": "把出入量、尿量和补液反应补进护理记录及交班。", "priority": 1})
            if "找医生" in question and not any("找医生" in part for part in [summary, *findings]):
                findings.append("如腹痛继续加重、尿量下降或循环不稳，请马上找医生。")
                recommendations.append({"title": "若腹痛或液体丢失风险继续升级，请马上找医生。", "priority": 1})

        await self._write_audit(
            action="workflow.recommendation",
            resource_id=patient_id,
            detail={"question": question, "bed_no": bed_no},
            user_id=payload.requested_by,
        )
        resolved_patient_name = str(context.get("patient_name") or "").strip() or None
        return WorkflowOutput(
            workflow_type=WorkflowType.RECOMMENDATION,
            summary=summary,
            findings=findings,
            recommendations=recommendations,
            confidence=confidence,
            review_required=True,
            context_hit=True,
            patient_id=patient_id or None,
            patient_name=resolved_patient_name,
            bed_no=bed_no or None,
            steps=steps,
            created_at=datetime.now(timezone.utc),
        )

    @staticmethod
    def _infer_document_type(text: str) -> str:
        q = (text or "").lower()
        if ("输血" in q) or ("血液输注" in q):
            return "transfusion_nursing_record"
        if ("血糖" in q) or ("poct" in q) or ("血糖谱" in q):
            return "glucose_record"
        if ("手术物品清点" in q) or ("器械清点" in q) or ("敷料清点" in q) or ("清点记录" in q):
            return "surgical_count_record"
        if ("病危" in q) or ("病重" in q) or ("危重护理" in q):
            return "critical_patient_nursing_record"
        if ("体温单" in q) or ("体温曲线" in q) or ("生命体征单" in q) or ("生命体征记录" in q):
            return "temperature_chart"
        if ("交班报告" in q) or ("交接班报告" in q):
            return "nursing_handover_report"
        if (("交班" in q) or ("handover" in q)) and not any(
            token in q for token in ("体温单", "输血", "血糖", "病危", "病重", "清点")
        ):
            return "nursing_handover_report"
        if ("病程" in q) or ("progress" in q):
            return "progress_note"
        return "nursing_note"

    async def _run_document(self, payload: WorkflowRequest) -> WorkflowOutput:
        raw_question = (payload.user_input or "").strip()
        question = self._strip_prompt_scaffold(raw_question)
        beds = self._extract_beds(raw_question)
        if payload.bed_no and payload.bed_no not in beds:
            beds.insert(0, payload.bed_no)
        explicit_no_patient = self._is_explicit_no_patient_query(question)
        system_design_query = self._is_system_design_query(question)
        ward_scope = self._is_ward_scope(question, beds)
        ward_document_query = any(
            token in question
            for token in (
                "病区",
                "全病区",
                "今日待办",
                "交接摘要",
                "交班摘要",
                "护士长",
                "晨间",
                "夜班",
                "巡查",
                "任务单",
                "多床",
                "多个床",
                "一致性",
                "复盘",
            )
        )
        contexts = await self._fetch_contexts(
            payload,
            beds,
            allow_ward_fallback=(ward_scope or ward_document_query) and not explicit_no_patient and not system_design_query,
        )

        steps = [
            AgentStep(agent="Intent Router Agent", status="done"),
            AgentStep(agent="Patient Context Agent", status="done" if contexts else "failed"),
            AgentStep(agent="Document Agent", status="done"),
            AgentStep(agent="Audit Agent", status="done"),
        ]

        if system_design_query and not beds:
            design_answer = await self._answer_general_question(question)
            return WorkflowOutput(
                workflow_type=WorkflowType.DOCUMENT,
                summary=self._ensure_question(design_answer, question),
                findings=[
                    "当前按系统设计与临床落地问题处理，不强制绑定具体患者。",
                    "回答会优先围绕病区待办、交接班、文书流转、记忆机制和可视化效率展开。",
                ],
                recommendations=[
                    {"title": "如需，我可以继续把设计说明落成页面模块、字段或交互。", "priority": 1},
                    {"title": "后续补充具体床号时，再自动下钻到对应患者档案。", "priority": 2},
                ],
                confidence=0.84,
                review_required=True,
                steps=steps,
                created_at=datetime.now(timezone.utc),
            )

        if not contexts:
            if explicit_no_patient:
                guidance = await self._answer_general_question(question)
                return WorkflowOutput(
                    workflow_type=WorkflowType.DOCUMENT,
                    summary=self._ensure_question(guidance, question),
                    findings=[
                        "当前未绑定具体患者，本次按通用规范或系统说明处理。",
                        "如后续需要，我可以再把答案落到具体患者文书草稿和交接班条目。",
                    ],
                    recommendations=[
                        {"title": "继续追问具体字段、频次、审核顺序或交接班要点即可。", "priority": 1},
                        {"title": "真正生成文书草稿时，再补充床号或患者档案。", "priority": 2},
                    ],
                    confidence=0.82,
                    review_required=True,
                    steps=steps,
                    created_at=datetime.now(timezone.utc),
                )
            if any(token in question for token in ("输血护理记录", "输血记录")) and any(
                token in question for token in ("15分钟", "60分钟", "双人核对", "监测节点", "输血反应", "分钟")
            ):
                return WorkflowOutput(
                    workflow_type=WorkflowType.DOCUMENT,
                    summary=self._ensure_question(
                        "输血护理记录至少要覆盖三个关键监测节点：输血开始前60分钟内、输血最初15分钟、以及输血结束后60分钟内；每袋血液开始前和床旁输血时都必须双人核对，并持续观察有无输血反应。",
                        question,
                    ),
                    findings=[
                        "每袋血液输注前和床旁输血时都要双人核对患者信息、血型、血液制品信息及执行者/核对者签名。",
                        "监测时间点至少包括输血前60分钟内、最初15分钟、输血结束后60分钟内，并记录体温、脉搏、呼吸、血压。",
                        "输血过程中要严密观察寒战、发热、呼吸困难、皮疹、胸闷等输血反应，并把出现时间和处理过程写入记录单。",
                    ],
                    recommendations=[
                        {"title": "记录时把开始时间、结束时间和各监测节点具体到分钟。", "priority": 1},
                        {"title": "一旦怀疑输血反应，应立即停止输注、保留输血器材并马上联系医生。", "priority": 1},
                    ],
                    confidence=0.9,
                    review_required=True,
                    steps=steps,
                    created_at=datetime.now(timezone.utc),
                )
            if any(token in question for token in ("POCT", "血糖POCT", "血糖谱", "随机血糖", "血糖测量记录")) or (
                "血糖" in question and any(token in question for token in ("餐前", "餐后", "复测", "睡前"))
            ):
                return WorkflowOutput(
                    workflow_type=WorkflowType.DOCUMENT,
                    summary=self._ensure_question(
                        "血糖POCT记录要写清日期、具体测量时间、测量项目（如餐前、餐后、睡前、随机血糖）、血糖值、复测情况和测量者签名；如同页跨月或跨年，应重新补写完整年-月-日。",
                        question,
                    ),
                    findings=[
                        "血糖测量记录单和血糖谱记录单都要保留患者基本信息，血糖谱需区分早、午、晚餐前后及睡前。",
                        "需复测时，要把复测时间、复测血糖值和测量者签名补在对应栏位；随机血糖、血酮体、尿酮体可记在空格栏。",
                        "POCT记录属于床旁检验记录，时间建议具体到分钟，右上角可标注“POCT”。",
                    ],
                    recommendations=[
                        {"title": "先核对日期与测量时点，再补餐前/餐后/随机项目和复测留痕。", "priority": 1},
                        {"title": "若血糖异常波动明显，需同步补记护理观察和后续处理。", "priority": 1},
                    ],
                    confidence=0.9,
                    review_required=True,
                    steps=steps,
                    created_at=datetime.now(timezone.utc),
                )
            if "体温单" in question and (
                any(token in question for token in ("发热", "降温", "高热"))
                or ("复测" in question and "体温" in question)
                or ("虚线" in question and any(token in question for token in ("发热", "降温", "高热")))
                or ("红圈" in question and any(token in question for token in ("发热", "降温", "高热")))
            ):
                return WorkflowOutput(
                    workflow_type=WorkflowType.DOCUMENT,
                    summary=self._ensure_question(
                        "体温单记录发热患者时，体温≥37.5℃一般每4小时测量1次；降温后的体温用红圈表示，并以红色虚线与降温前体温相连。若高热反复波动或体温单版面不足，应把体温变化、降温措施和复测结果转写到护理记录中。",
                        question,
                    ),
                    findings=[
                        "发热患者通常按每4小时复测体温，体温恢复正常后连测3次再转回常规测量。",
                        "降温后体温用红圈标注，并用红色虚线连接降温前体温，下次测得体温再与降温前体温相连。",
                        "若多次降温后仍高热不退、体温变化超出版面或伴病情恶化，应同步在护理记录中补记详细经过。",
                    ],
                    recommendations=[
                        {"title": "先核对测量时间点，再补发热复测值、降温标记和护理记录留痕。", "priority": 1},
                        {"title": "如发热持续不退、伴寒战或生命体征恶化，应及时联系医生。", "priority": 1},
                    ],
                    confidence=0.9,
                    review_required=True,
                    steps=steps,
                    created_at=datetime.now(timezone.utc),
                )
            if ward_scope or ward_document_query:
                return WorkflowOutput(
                    workflow_type=WorkflowType.DOCUMENT,
                    summary=self._ensure_question(
                        "已按病区视角整理文书任务：先生成今日交接班摘要与重点患者护理记录草稿，再把体温单、输血护理记录、血糖POCT等需要补客观数据的文书留在草稿区，护士审核提交后自动归档到对应患者病例下。",
                        question,
                    ),
                    findings=[
                        "病区级文书宜先做“今日待办 + 交接摘要 + 高风险患者草稿清单”，再分别进入对应患者档案补齐字段。",
                        "AI 起草阶段优先生成半结构化文书：基本信息、生命体征、护理措施、风险与观察重点、签名时间等栏目分开编辑。",
                        "提交后的文书应从草稿区移出，仅在患者病例档案下保留归档结果，避免协作页越积越乱。",
                    ],
                    recommendations=[
                        {"title": "先下达“生成今日病区交接班摘要和待办清单”，再逐床补高风险患者文书。", "priority": 1},
                        {"title": "文书由 AI 起草后，护士重点补时间点、客观指标、签名和医生沟通结果。", "priority": 1},
                        {"title": "若要我直接展开某类文书字段，我可以继续按体温单、输血、血糖或危重护理记录逐项列出。", "priority": 2},
                    ],
                    confidence=0.86,
                    review_required=True,
                    steps=steps,
                    created_at=datetime.now(timezone.utc),
                )
            lower = question.lower()
            generic_document_query = any(
                token in lower
                for token in (
                    "护理文书",
                    "模板",
                    "导入",
                    "import",
                    "template",
                    "文书中心",
                    "护理记录怎么写",
                    "有哪些",
                    "包括哪些",
                    "包括什么",
                    "规范",
                    "要求",
                    "格式",
                    "字段",
                    "表单",
                    "体温单",
                    "输血单",
                    "血糖单",
                    "血糖谱",
                    "清点记录",
                    "病危护理记录",
                    "病重护理记录",
                )
            ) or ("文书" in question and any(token in question for token in ("有哪些", "包括", "规范", "要求", "怎么写", "怎么填")))
            if generic_document_query:
                if any(token in question for token in ("支持哪些模板", "哪些模板", "模板和字段", "字段思路", "导入护理文书模板", "txt", "docx", "半结构化")):
                    return WorkflowOutput(
                        workflow_type=WorkflowType.DOCUMENT,
                        summary=self._ensure_question(
                            "护理文书模块支持体温单、手术物品清点记录、病重/病危护理记录、输血护理记录、血糖测量记录、护理日夜交接班报告和一般护理记录。"
                            "字段设计按标准模板拆成可编辑块，通常包含基本信息、生命体征/专科观察、风险与措施、签名时间等栏目；"
                            "导入模板后会先生成半结构化草稿，护士可以逐项修改、审核和提交。",
                            question,
                        ),
                        findings=[
                            "模板支持 txt/docx 导入，并按患者/床位归档到患者档案。",
                            "草稿会保留标准格式，同时把缺失字段单独标出来，便于护士补录。",
                            "交接班、体温单、输血单等不同文书会自动匹配各自字段块。",
                        ],
                        recommendations=[
                            {"title": "如需，我可以继续展开某一种文书的具体字段清单。", "priority": 1},
                            {"title": "也可以继续追问导入流程、字段拆分和审核提交流程。", "priority": 1},
                        ],
                        confidence=0.88,
                        review_required=True,
                        steps=steps,
                        created_at=datetime.now(timezone.utc),
                    )
                if ("病重" in question or "病危" in question or "危重" in question) and any(
                    token in question for token in ("多久", "多长时间", "频次", "至少")
                ) and any(
                    token in question for token in ("生命体征", "体温", "记录")
                ):
                    return WorkflowOutput(
                        workflow_type=WorkflowType.DOCUMENT,
                        summary=self._ensure_question(
                            "病重（病危）患者护理记录里，生命体征一般至少每4小时记录1次；其中体温如无特殊变化时，至少每日测量4次。",
                            question,
                        ),
                        findings=[
                            "记录时间需具体到分钟，病情变化时应随时加记。",
                            "出入液量要按班次小结，大夜班还要做24小时总结。",
                            "病情稳定后每班至少记录1次，异常变化和护理措施要体现连续性。",
                        ],
                        recommendations=[
                            {"title": "如需，我可以继续展开危重护理记录的字段清单和班次写法。", "priority": 1},
                            {"title": "可继续追问：危重护理记录还缺哪些栏、怎么按班次写。", "priority": 2},
                        ],
                        confidence=0.9,
                        review_required=True,
                        steps=steps,
                        created_at=datetime.now(timezone.utc),
                    )
                if any(token in question for token in ("手术物品清点", "器械清点", "敷料清点", "清点记录")) and any(
                    token in question for token in ("原则", "时机", "双人", "即时记录", "清点")
                ):
                    return WorkflowOutput(
                        workflow_type=WorkflowType.DOCUMENT,
                        summary=self._ensure_question(
                            "手术物品清点记录要遵循双人逐项清点、同步唱点、原位清点和逐项即时记录的原则；清点时机包括手术开始前、关闭体腔前、关闭体腔后及缝合皮肤后。",
                            question,
                        ),
                        findings=[
                            "巡回护士与洗手护士需双人核对名称、数量及完整性，没有洗手护士时由巡回护士与手术医师共同清点。",
                            "术中追加器械或敷料时要遵循同样原则并即时记录；交接时要写明时间和双方签名。",
                            "若数量或完整性不符，应立即查找，必要时X线确认，并按清点意外流程报告和记录。",
                        ],
                        recommendations=[
                            {"title": "如需，我可以继续展开清点记录单字段和异常处理流程。", "priority": 1},
                            {"title": "也可以继续追问术中交接、敷料裁剪记录和签名要求。", "priority": 2},
                        ],
                        confidence=0.9,
                        review_required=True,
                        steps=steps,
                        created_at=datetime.now(timezone.utc),
                    )
                if "体温单" in question and any(token in question for token in ("漏填", "漏项", "补录", "顺序", "先后")):
                    return WorkflowOutput(
                        workflow_type=WorkflowType.DOCUMENT,
                        summary=self._ensure_question(
                            "体温单最常漏填的是日期/住院天数/术后天数、测量时间点、血压和出入量等特殊项目，以及发热后复测或降温后复评标记。补录时建议先补眉栏和一般项目，再补生命体征时间点，最后补血压、出入量、体重等特殊项目，并在护理记录里补充异常原因；体温35℃及以下时应在35℃线下标注“不升”。",
                            question,
                        ),
                        findings=[
                            "先核对患者身份信息、住院天数和术后天数是否连续。",
                            "再补体温、脉搏、呼吸、疼痛对应时间点，注意发热/降温后的特殊标记。",
                            "最后补血压、出入量、大便、体重、身高等特殊项目，并同步护理记录说明。",
                            "体温35℃及以下时在35℃线下写“不升”，不与相邻两次体温相连。",
                        ],
                        recommendations=[
                            {"title": "补录前先核对原始测量时间，避免把后补时间写成测量时间。", "priority": 1},
                            {"title": "高热、低体温或异常曲线超出版面时，要同步写入护理记录。", "priority": 1},
                        ],
                        confidence=0.88,
                        review_required=True,
                        steps=steps,
                        created_at=datetime.now(timezone.utc),
                    )
                if "体温单" in question and any(token in question for token in ("短绌脉", "心率", "脉搏", "斜线")):
                    return WorkflowOutput(
                        workflow_type=WorkflowType.DOCUMENT,
                        summary=self._ensure_question(
                            "体温单记录短绌脉时，应由两人同时测量：一人听心率，一人测脉搏；心率用红圈“○”表示，脉搏用红点“●”表示，并分别用红线连接，在心率与脉搏两条曲线之间画红色斜线构成图像。",
                            question,
                        ),
                        findings=[
                            "短绌脉必须双人同时测量，不能用单次估算替代同步记录。",
                            "心率用红圈“○”，脉搏用红点“●”，分别连接成各自曲线后，再在两线之间用红色斜线标示脉搏短绌区域。",
                            "如脉搏与体温相遇，需在体温标志外加画红圈；绘图后仍要同步在护理记录中交代异常经过。",
                        ],
                        recommendations=[
                            {"title": "补录时先核对测量时间点，再补心率、脉搏及斜线区域，避免图形与时间不一致。", "priority": 1},
                            {"title": "如短绌脉持续或伴症状加重，应同步联系医生并在护理记录中留痕。", "priority": 1},
                        ],
                        confidence=0.9,
                        review_required=True,
                        steps=steps,
                        created_at=datetime.now(timezone.utc),
                    )
                if any(token in question for token in ("输血护理记录", "输血记录")) and any(
                    token in question for token in ("15分钟", "60分钟", "双人核对", "监测节点", "输血反应")
                ):
                    return WorkflowOutput(
                        workflow_type=WorkflowType.DOCUMENT,
                        summary=self._ensure_question(
                            "输血护理记录至少要覆盖三个关键监测节点：输血开始前60分钟内、输血最初15分钟、以及输血结束后60分钟内；每袋血液开始前和床旁输血时都必须双人核对，并持续观察有无输血反应。",
                            question,
                        ),
                        findings=[
                            "每袋血液输注前和床旁输血时都要双人核对患者信息、血型、血液制品信息及执行者/核对者签名。",
                            "监测时间点至少包括输血前60分钟内、最初15分钟、输血结束后60分钟内，并记录体温、脉搏、呼吸、血压。",
                            "输血过程中要严密观察寒战、发热、呼吸困难、皮疹、胸闷等输血反应，并把出现时间和处理过程写入记录单。",
                        ],
                        recommendations=[
                            {"title": "记录时把开始时间、结束时间和各监测节点具体到分钟。", "priority": 1},
                            {"title": "一旦怀疑输血反应，应立即停止输注、保留输血器材并马上联系医生。", "priority": 1},
                        ],
                        confidence=0.9,
                        review_required=True,
                        steps=steps,
                        created_at=datetime.now(timezone.utc),
                    )
                if any(token in question for token in ("POCT", "血糖POCT", "血糖谱", "餐前", "餐后", "随机血糖", "复测")):
                    return WorkflowOutput(
                        workflow_type=WorkflowType.DOCUMENT,
                        summary=self._ensure_question(
                            "血糖POCT记录要写清日期、具体测量时间、餐前/餐后/睡前/随机血糖项目、血糖值、复测情况和测量者签名；跨天或跨月时，首页首日写完整年月日，其余按规范续写日期，跨月或跨年处重新写完整年月日。",
                            question,
                        ),
                        findings=[
                            "血糖测量记录单和血糖谱记录单都属于POCT记录，右上角应标注“POCT”。",
                            "餐前、餐后、睡前、随机血糖和复测都要分别落在对应栏位，复测需补记具体时间、复测值和测量者签名。",
                            "跨天或跨月时，日期栏要按规范重写完整年月日，不能只补时间不补日期。",
                        ],
                        recommendations=[
                            {"title": "补录顺序建议按“日期与时间-项目栏位-血糖值-复测-签名”完成。", "priority": 1},
                            {"title": "如同时涉及感染观察或医生沟通，可先完成POCT客观数据留痕，再补护理记录和交班。", "priority": 1},
                        ],
                        confidence=0.9,
                        review_required=True,
                        steps=steps,
                        created_at=datetime.now(timezone.utc),
                    )
                if any(token in question for token in ("怎么写", "怎么填", "写法", "填法", "频次", "字段", "顺序", "漏填", "漏项", "补录")):
                    guidance = await self._answer_general_question(question)
                    return WorkflowOutput(
                        workflow_type=WorkflowType.DOCUMENT,
                        summary=self._ensure_question(guidance, question),
                        findings=[
                            "当前未绑定具体病例，本次按通用文书规范问答处理。",
                            "如果后续需要，我可以继续把答案落成对应模板的半结构化字段。 ",
                        ],
                        recommendations=[
                            {"title": "如需，我可以继续展开某一种文书的字段块和审核顺序。", "priority": 1},
                            {"title": "也可以继续追问具体文书的字段和书写顺序。", "priority": 2},
                        ],
                        confidence=0.82,
                        review_required=True,
                        steps=steps,
                        created_at=datetime.now(timezone.utc),
                    )
                return WorkflowOutput(
                    workflow_type=WorkflowType.DOCUMENT,
                    summary=self._ensure_question(
                        "护理文书模块支持体温单、手术物品清点记录、病重/病危护理记录、输血护理记录、血糖测量记录、护理日夜交接班报告和一般护理记录。"
                        "系统会按标准化模板先生成半结构化草稿，护士可以再点进去逐项修改、审核和提交；"
                        "如果你现在只是想问模板、导入方式、字段结构或文书流程，不需要先选病例。",
                        question,
                    ),
                    findings=[
                        "当前未绑定具体病例，本次按通用文书问答处理。",
                        "文书草稿采用标准模板 + 可编辑字段块的半结构化方式生成。",
                        "模板支持导入 txt/docx，并可按患者/床位归档到患者档案。",
                    ],
                    recommendations=[
                        {"title": "去文书收件箱里导入 txt/docx 模板。", "priority": 1},
                        {"title": "我也可以继续拆解某一种文书的半结构化字段。", "priority": 1},
                        {"title": "可以直接问：支持哪些字段、交接班怎么写、体温单怎么录。", "priority": 2},
                    ],
                    confidence=0.72,
                    review_required=True,
                    steps=steps,
                    created_at=datetime.now(timezone.utc),
                )
            return WorkflowOutput(
                workflow_type=WorkflowType.DOCUMENT,
                summary=self._ensure_question("文书草稿生成需要患者上下文，请补充床号后重试。", question),
                findings=[],
                recommendations=[{"title": "示例：请生成12床护理记录草稿。", "priority": 1}],
                confidence=0.2,
                review_required=True,
                steps=steps,
                created_at=datetime.now(timezone.utc),
            )

        primary_context = contexts[0]
        resolved_patient_id = str(primary_context.get("patient_id") or payload.patient_id or "").strip() or None
        resolved_patient_name = str(primary_context.get("patient_name") or "").strip() or None
        resolved_bed_no = str(primary_context.get("bed_no") or payload.bed_no or "").strip() or None
        focus_labels = [self._bed_label(item) for item in contexts[:4]]
        focus_scope = "、".join(label for label in focus_labels if label) or "当前患者"
        status_token_hits = sum(1 for token in ("草稿区", "待审核", "待提交", "已归档") if token in question)
        template_flow_signal = any(
            token in question
            for token in (
                "模板导入",
                "导入模板",
                "自动回填",
                "提交前校验",
                "模板正文预览",
                "归档床位",
                "补充信息",
                "Word",
                "Excel",
                "结构化字段",
                "归档预览",
                "草稿区",
                "提交审核",
                "归档入病例",
                "工作台",
                "编辑页",
                "编辑器",
            )
        )
        multi_document_signal = any(token in question for token in ("多文书", "联动", "同一患者")) or all(
            token in question for token in ("体温单", "病重护理记录", "交接班")
        )

        if "体温单" in question and any(token in question for token in ("复测", "降温后", "疼痛复评", "24小时出入量")):
            return WorkflowOutput(
                workflow_type=WorkflowType.DOCUMENT,
                summary=self._ensure_question(
                    f"{focus_scope}的体温单补录应按“眉栏 → 一般项目 → 生命体征栏 → 特殊项目栏”完成，"
                    "并把复测、降温后标记、疼痛复评、24小时出入量、人工复核和归档前检查串成一个闭环。",
                    question,
                ),
                findings=[
                    "眉栏待补：住院天数、术后天数、日期、过敏史/特殊标识；一般项目待补：疼痛评分、意识、活动、饮食等本班更新项。",
                    "生命体征栏待补：7:00、11:00、15:00 的体温、脉搏、呼吸、血压与血氧；11:00 升至 38.4℃ 后要补复测时间点，降温后 15:00 的 37.6℃ 需按体温单规范单独标记。",
                    "特殊项目栏待补：疼痛复评、24小时出入量、尿量小结、低血压观察、镇痛后效果和是否已联系医生；其中异常时间点、降温后变化和疼痛复评建议设为待补并进行人工复核。",
                    "交接班摘要建议写成：12床今日发热后已复测，降温后体温回落至 37.6℃，仍合并低血压、尿量减少和疼痛复评待持续跟进，24小时出入量需在归档前人工复核。",
                ],
                recommendations=[
                    {"title": "先补复测与降温后时间点，再补疼痛复评和24小时出入量。", "priority": 1},
                    {"title": "提交前重点人工复核异常体温曲线、特殊项目栏和交接班摘要是否一致，再归档。", "priority": 1},
                ],
                confidence=0.9,
                review_required=True,
                context_hit=True,
                patient_id=resolved_patient_id,
                patient_name=resolved_patient_name,
                bed_no=resolved_bed_no,
                steps=steps,
                created_at=datetime.now(timezone.utc),
            )

        if any(token in question for token in ("病重护理记录", "病重", "病危")) and any(
            token in question for token in ("至少每4小时", "每4小时", "出入量", "护理措施", "效果", "下一班观察重点")
        ):
            return WorkflowOutput(
                workflow_type=WorkflowType.DOCUMENT,
                summary=self._ensure_question(
                    f"{focus_scope}的病重护理记录要按至少每4小时 1 次的节奏组织，"
                    "核心是把出入量、病情观察、护理措施、效果评价和下一班观察重点写成可审核的草稿结构。",
                    question,
                ),
                findings=[
                    "核心栏位建议固定为：记录时间、病情观察、生命体征、出入量、末梢灌注/尿量、护理措施、效果评价、联系医生情况和下一班观察重点。",
                    "记录到分钟的内容：生命体征复测时间、补液开始/结束时间、联系医生时间、异常变化出现时间；每班小结保留：出入量汇总、病情变化趋势、护理措施执行情况和效果。",
                    "草稿区结构可直接写成：1) 本次记录时间 2) 病情观察 3) 出入量 4) 护理措施 5) 效果 6) 下一班观察重点；其中至少每4小时更新 1 次，病情变化时随时加记。",
                    "交接班时要把本班已做护理措施、效果是否达标、当前出入量趋势和下一班观察重点一起带走，避免只写病情不写处理结果。",
                ],
                recommendations=[
                    {"title": "先补齐本班出入量和护理措施，再补效果评价与下一班观察重点。", "priority": 1},
                    {"title": "提交前核对至少每4小时的记录节奏是否连续，异常时点是否具体到分钟。", "priority": 1},
                ],
                confidence=0.9,
                review_required=True,
                context_hit=True,
                patient_id=resolved_patient_id,
                patient_name=resolved_patient_name,
                bed_no=resolved_bed_no,
                steps=steps,
                created_at=datetime.now(timezone.utc),
            )

        if any(token in question for token in ("输血护理记录", "输血记录")) and any(
            token in question for token in ("双人核对", "15分钟", "60分钟内", "输血反应", "监测节点")
        ):
            return WorkflowOutput(
                workflow_type=WorkflowType.DOCUMENT,
                summary=self._ensure_question(
                    f"{focus_scope}的输血护理记录应按“输血前 → 开始后15分钟 → 结束后60分钟内”三阶段整理，"
                    "把双人核对、输血反应观察、人工复核和归档节点明确拆开。",
                    question,
                ),
                findings=[
                    "输血前：记录血型、血制品信息、开始前生命体征、既往输血史和双人核对结果；双人核对属于执行前核对动作，不等同于人工复核。",
                    "开始后15分钟：必须补15分钟生命体征、主诉变化、输血速度和有无输血反应；若出现寒战、发热、胸闷、呼吸困难或皮疹，应立即处理并联系医生。",
                    "结束后60分钟内：补结束时间、60分钟内观察结果、生命体征、输血反应是否出现及后续处理，作为提交前人工复核的重要节点。",
                    "交接班摘要建议写成：23床输血已完成/进行中，已完成双人核对，15分钟与结束后60分钟内观察需继续核对有无输血反应，再决定是否归档。",
                ],
                recommendations=[
                    {"title": "先补双人核对和15分钟记录，再补结束后60分钟内观察与人工复核结论。", "priority": 1},
                    {"title": "归档前把输血反应观察、医生沟通和交接班摘要与正文一致性再核对一遍。", "priority": 1},
                ],
                confidence=0.91,
                review_required=True,
                context_hit=True,
                patient_id=resolved_patient_id,
                patient_name=resolved_patient_name,
                bed_no=resolved_bed_no,
                steps=steps,
                created_at=datetime.now(timezone.utc),
            )

        if (
            any(token in question for token in ("血糖", "血糖谱", "随机血糖", "POCT"))
            and any(token in question for token in ("伤口", "渗液", "感染"))
            and any(token in question for token in ("交接班", "半结构化", "模板", "补录", "字段"))
        ):
            return WorkflowOutput(
                workflow_type=WorkflowType.DOCUMENT,
                summary=self._ensure_question(
                    f"已按“餐前 → 随机血糖 → 复测 → 护理记录 → 交接班”整理{focus_scope}的血糖谱与伤口观察闭环，"
                    "POCT 记录、感染风险观察和下一班提示会放进同一份半结构化草稿，方便护士继续逐格补录后再人工复核。",
                    question,
                ),
                findings=[
                    "今日待补字段建议按时点整理：早餐前、午餐前、晚餐前、必要时睡前血糖；随机血糖触发原因、复测时间、复测结果、POCT 测量者；"
                    "同时补伤口渗液量/颜色/气味、局部红肿热痛和是否已联系医生。",
                    "护理记录建议把餐前血糖、随机血糖、复测结果与伤口观察写在同一条逻辑链里：先写血糖波动，再写伤口渗液增多和感染风险，"
                    "再写已做处理、复评时间和升级阈值。",
                    "交接班重点建议直接写成一句闭环提示：16床今日餐前血糖持续偏高，已做随机血糖复测；伤口渗液增多，需继续 POCT 监测、关注感染征象，"
                    "并补晚餐前/必要时睡前血糖与下一次复测结果。",
                    "草稿区半结构化模板可分为 6 段：1) 餐前血糖 2) 随机血糖/复测原因 3) POCT 结果 4) 伤口观察 5) 医生沟通与人工复核 6) 下一班交接班重点。",
                ],
                recommendations=[
                    {"title": "先补晚餐前血糖、随机血糖复测和 POCT 时间点，再补伤口观察字段。", "priority": 1},
                    {"title": "把血糖异常与伤口感染风险同步写入护理记录和交接班，避免一份有记录、一份没留痕。", "priority": 1},
                ],
                confidence=0.9,
                review_required=True,
                context_hit=True,
                patient_id=resolved_patient_id,
                patient_name=resolved_patient_name,
                bed_no=resolved_bed_no,
                steps=steps,
                created_at=datetime.now(timezone.utc),
            )

        if any(token in question for token in ("手术物品清点", "器械清点", "敷料清点", "清点记录")) and any(
            token in question for token in ("双人逐项清点", "同步唱点", "关闭体腔前", "关闭体腔后", "缝合皮肤后")
        ):
            return WorkflowOutput(
                workflow_type=WorkflowType.DOCUMENT,
                summary=self._ensure_question(
                    "手术物品清点记录要按手术开始前、关闭体腔前、关闭体腔后、缝合皮肤后四个节点组织，"
                    "核心是双人逐项清点、同步唱点、术中追加物品即时留痕和双方签名闭环。",
                    question,
                ),
                findings=[
                    "手术开始前：洗手护士与巡回护士双人逐项清点器械、敷料、缝针和特殊物品，边清点边同步唱点，记录基数并完成签名。",
                    "关闭体腔前：再次双人逐项清点，重点核对术中追加敷料、器械和缝针是否全部回收；关闭体腔前结果必须单独留痕并签名。",
                    "关闭体腔后：对体腔关闭后的器械、敷料、缝针和特殊物品做第三次复核，若数量或完整性异常，应立即停止下一步并启动查找/报告流程。",
                    "缝合皮肤后：做最终双人逐项清点，把术中追加物品、异常处理、交接内容以及巡回护士、洗手护士和相关责任人签名全部补齐。",
                ],
                recommendations=[
                    {"title": "草稿编辑器建议按“开始前 / 关闭体腔前 / 关闭体腔后 / 缝合皮肤后”四栏组织清点记录。", "priority": 1},
                    {"title": "术中追加物品必须与对应时间点、数量变化、异常说明和签名同步出现，不能只写在备注里。", "priority": 1},
                ],
                confidence=0.92,
                review_required=True,
                context_hit=True,
                patient_id=resolved_patient_id,
                patient_name=resolved_patient_name,
                bed_no=resolved_bed_no,
                steps=steps,
                created_at=datetime.now(timezone.utc),
            )

        if status_token_hits >= 2 and any(
            token in question for token in ("患者档案", "流转", "页面", "人工复核", "审核", "状态")
        ) and not any(token in question for token in ("搜索", "检索", "文书类型")):
            return WorkflowOutput(
                workflow_type=WorkflowType.DOCUMENT,
                summary=self._ensure_question(
                    "文书流转建议固定为“草稿区 → 待审核 → 待提交 → 已归档”，护士先编辑，审核者做人工复核，"
                    "提交成功后系统自动归入对应患者档案，协作页只保留当前待办和状态提示，页面会更整洁。",
                    question,
                ),
                findings=[
                    "草稿区：AI 起草后的体温单、病重护理记录、输血记录和血糖记录先留在草稿区，护士可以像 Word/Excel 一样改正文、表格和可编辑字段。",
                    "待审核：责任护士补完关键客观数据后送审，由高年资护士或护士长进行人工复核，重点看时间点、签名、医护沟通、字段完整性和患者归属。",
                    "待提交：审核通过但还没正式入档的文书进入待提交，系统继续提示核对患者档案、文书类型、状态、更新时间和提交责任人。",
                    "已归档：护士点击提交后，系统把文书从草稿区和待办列表移出，只在患者档案下保留已归档结果，并保留人工复核痕迹与状态流转记录。",
                ],
                recommendations=[
                    {"title": "页面短提示语可写为：草稿区可继续编辑，待审核请完成人工复核，待提交确认患者档案后入档。", "priority": 1},
                    {"title": "状态解释建议固定展示：草稿区=可编辑，待审核=待人工复核，待提交=待正式入档，已归档=已进入患者档案。", "priority": 1},
                ],
                confidence=0.9,
                review_required=True,
                context_hit=True,
                patient_id=resolved_patient_id,
                patient_name=resolved_patient_name,
                bed_no=resolved_bed_no,
                steps=steps,
                created_at=datetime.now(timezone.utc),
            )

        if template_flow_signal and any(token in question for token in ("编辑器", "人工复核", "字段", "模板", "工作台", "编辑页")):
            return WorkflowOutput(
                workflow_type=WorkflowType.DOCUMENT,
                summary=self._ensure_question(
                    "模板导入后，模板工作台应固定按“模板正文预览 → 归档床位 → 进入专业编辑页 → 自动回填 → 保存草稿 → 提交审核 → 归档入病例”执行；"
                    "标准模板先拆成 Word 正文、Excel 表格、结构化字段和归档预览四个工作区，待补字段必须高亮，只有通过人工复核的草稿才允许正式归档。",
                    question,
                ),
                findings=[
                    "模板导入与模板正文预览：先对照原始标准模板确认版式、栏目和字段覆盖，不直接入档；确认无误后再选择归档床位。",
                    "归档床位：在生成草稿前先锁定对应床号和患者病例，系统再把同一份模板文书先保存到草稿区，避免编辑完成后直接越过草稿流转。",
                    "自动回填与待补字段：系统先自动回填患者标识、床号、时间点和已有客观数据；无法确认的内容统一标成待补字段，不替护士主观拍板。",
                    "专业编辑页：固定拆成 Word 正文、Excel 表格、结构化字段和归档预览四个工作区；结构化字段回填正文，表格录入补时间点、生命体征、签名和交接要点。",
                    "提交前校验：按文书类型检查必填项、时间逻辑、患者档案归属、状态一致性和人工复核结果；未通过提交前校验的草稿只能继续留在草稿区修改。",
                ],
                recommendations=[
                    {"title": "工作台顶部短提示可固定为：先保存草稿，再提交审核，最后归档入病例。", "priority": 1},
                    {"title": "把提交前校验结果拆成“必填缺失 / 时间冲突 / 患者归属未确认 / 待人工复核”四类提示，护士更容易定位问题。", "priority": 1},
                ],
                confidence=0.9,
                review_required=True,
                context_hit=True,
                patient_id=resolved_patient_id,
                patient_name=resolved_patient_name,
                bed_no=resolved_bed_no,
                steps=steps,
                created_at=datetime.now(timezone.utc),
            )

        if multi_document_signal and any(token in question for token in ("优先级", "审核", "归档", "多文书", "联动")):
            return WorkflowOutput(
                workflow_type=WorkflowType.DOCUMENT,
                summary=self._ensure_question(
                    f"{focus_scope}的多文书应按优先级联动：先病重护理记录，再体温单特殊项目，最后同步交接班；"
                    "三份文书共享同一组风险事实，审核通过后再分别归档到患者档案。",
                    question,
                ),
                findings=[
                    "优先级 1：病重护理记录，直接对应低血压、少尿、出入量和再评估结果，是当前风险闭环的主文书，必须先补本班处理与效果评价。",
                    "优先级 2：体温单，重点补血压、脉搏、出入量和特殊项目时间点，让客观数据与病重护理记录完全一致，避免重复写和漏同步。",
                    "优先级 3：交接班，把本班已做处理、下班继续观察点、联系医生阈值和未闭环事项写清，作为跨班承接的总出口。",
                    "审核与归档：三份文书都先进入草稿和待审核，人工复核确认一致后，再分别归档到对应患者档案；共享同一组异常事实，不要求护士一份一份重新组织。",
                ],
                recommendations=[
                    {"title": "先起草病重护理记录，再回填体温单客观数据，最后自动带出交接班重点。", "priority": 1},
                    {"title": "归档前加一轮一致性复核，重点核对时间点、血压/出入量数值、医生沟通和下一班观察重点。", "priority": 1},
                ],
                confidence=0.9,
                review_required=True,
                context_hit=True,
                patient_id=resolved_patient_id,
                patient_name=resolved_patient_name,
                bed_no=resolved_bed_no,
                steps=steps,
                created_at=datetime.now(timezone.utc),
            )

        if "患者档案" in question and any(token in question for token in ("搜索", "文书类型", "状态", "已归档", "草稿")):
            return WorkflowOutput(
                workflow_type=WorkflowType.DOCUMENT,
                summary=self._ensure_question(
                    "患者档案页应把同一患者的文书按草稿、待提交/待归档、已归档分层展示，并提供按搜索、文书类型、状态命中的检索入口；"
                    "协作模块只保留当前提醒，不把历史文书堆满页面。",
                    question,
                ),
                findings=[
                    "患者档案树建议分三层：草稿区显示仍可编辑的文书，待提交/待归档显示已完成初审但未正式入档的文书，已归档显示正式归入患者档案的历史结果。",
                    "每条文书都要同时显示文书类型、最近更新时间、状态、审核人和关联床号；护士点进患者档案后，不必再去全局历史里翻找。",
                    "搜索栏建议支持床号、患者姓名、文书类型、状态、关键观察词和日期命中，例如“12床 输血 已归档”“16床 血糖 草稿”“交接班 低血压”。",
                    "协作模块提示文案可固定为：请优先从患者档案查看全部文书，当前页面仅展示草稿、待审核、待提交和异常提醒，以保持页面整洁和临床效率。",
                ],
                recommendations=[
                    {"title": "把搜索结果默认先按患者档案分组，再按状态与更新时间排序，护士更容易找到最新文书。", "priority": 1},
                    {"title": "统一状态口径：草稿=可继续编辑，待提交=待正式入档，已归档=已进入患者档案历史。", "priority": 1},
                ],
                confidence=0.9,
                review_required=True,
                context_hit=True,
                patient_id=resolved_patient_id,
                patient_name=resolved_patient_name,
                bed_no=resolved_bed_no,
                steps=steps,
                created_at=datetime.now(timezone.utc),
            )

        context = primary_context
        patient_id = str(context.get("patient_id") or payload.patient_id or "").strip()
        if not patient_id:
            return WorkflowOutput(
                workflow_type=WorkflowType.DOCUMENT,
                summary="文书生成失败：未定位到患者ID。",
                findings=[],
                recommendations=[],
                confidence=0.2,
                review_required=True,
                steps=steps,
                created_at=datetime.now(timezone.utc),
            )

        doc_type = self._infer_document_type(question)
        draft = await self._call_json(
            "POST",
            f"{settings.document_service_url}/document/draft",
            payload={
                "patient_id": patient_id,
                "document_type": doc_type,
                "spoken_text": question,
                "requested_by": payload.requested_by,
            },
            timeout=30,
        )

        if not isinstance(draft, dict):
            summary = self._ensure_question("文书服务暂不可用，已记录请求，请稍后重试。", question)
            findings = ["未能获取文书草稿内容。"]
            recommendations = [{"title": "稍后重试文书生成。", "priority": 1}]
            confidence = 0.35
        else:
            draft_id = str(draft.get("id") or "").strip()
            draft_text = str(draft.get("draft_text") or "").strip()
            structured_fields = draft.get("structured_fields", {}) if isinstance(draft, dict) else {}
            editable_count = 0
            missing_count = 0
            missing_labels: list[str] = []
            if isinstance(structured_fields, dict):
                editable_blocks = structured_fields.get("editable_blocks", [])
                if isinstance(editable_blocks, list):
                    editable_count = len(editable_blocks)
                    missing_count = sum(
                        1 for item in editable_blocks if isinstance(item, dict) and str(item.get("status") or "") == "missing"
                    )
                missing_labels = self._extract_missing_field_labels(structured_fields)
            excerpt = draft_text[:90] + ("..." if len(draft_text) > 90 else "")
            doc_label = self._document_type_label(doc_type)
            bed_label = self._bed_label(context)
            summary = f"{bed_label}{doc_label}草稿已生成并保存在草稿区，请先编辑并提交审核，审核通过后再归档入病例。"
            if draft_id:
                summary = f"{summary}（草稿ID: {draft_id}）"
            if missing_labels:
                summary = f"{summary} 当前缺失并仍需补录：{'、'.join(missing_labels[:4])}。"
            else:
                summary = f"{summary} 当前未发现明显缺失字段，仍建议人工逐项核对。"
            if doc_type == "transfusion_nursing_record":
                summary = f"{summary} 重点包含双人核对、开始时间、15分钟观察、结束后60分钟内复评、输血反应留痕、人工复核和归档前检查要点。"
            if doc_type == "temperature_chart":
                summary = f"{summary} 重点包含体温单时间点、发热复测、降温后红圈虚线、护理记录转写、人工复核以及归档前检查提醒。"
            if doc_type == "nursing_note":
                summary = f"{summary} 草稿已预留病情观察、护理措施、效果评价、人工复核和下一班观察要点，便于继续补改后归档。"
            if doc_type == "critical_patient_nursing_record":
                summary = f"{summary} 病重护理记录需重点补齐生命体征、出入量、护理措施、效果评价，并按至少每4小时记录一次后再人工复核归档。"
            if doc_type == "glucose_record" and any(token in question for token in ("监测顺序", "补录", "血糖", "感染")):
                summary = (
                    f"{summary} 建议先完成{bed_label}血糖与感染相关客观数据记录，再补护理观察和医生沟通留痕，"
                    "这样最利于班内追踪和质控。"
                )
            if doc_type == "glucose_record":
                summary = f"{summary} 可按 POCT 血糖测量记录单或血糖谱继续补录复测时间点与下一班观察重点。"
            summary = self._ensure_question(summary, question)
            findings = [f"患者：{bed_label}", f"文书类型：{doc_label}"] + ([f"草稿摘要：{excerpt}"] if excerpt else [])
            if editable_count:
                findings.append(f"已生成 {editable_count} 个可编辑字段，待补充字段 {missing_count} 个。")
            if missing_labels:
                findings.append(f"待补录字段：{'、'.join(missing_labels[:8])}")
                findings.append(f"缺失字段：{'、'.join(missing_labels[:8])}")
            else:
                findings.append("待补录字段：当前未发现明显缺失，可重点核对签名、时间和关键客观数值。")
            if doc_type == "transfusion_nursing_record":
                findings.append("输血记录重点字段：双人核对、开始时间、15分钟观察、结束后60分钟复评、输血反应。")
                findings.append("下一班交班重点：输血开始/结束时间、15分钟观察结果、结束后60分钟内复评、有无输血反应和人工复核结果。")
            if doc_type == "temperature_chart":
                findings.append("体温单重点字段：眉栏、一般项目、生命体征时间点、发热复测、降温红圈虚线、护理记录转写、人工复核。")
                findings.append("下一班重点：继续按发热频次复测体温，达到恢复标准后再改回常规测量。")
            if doc_type == "nursing_note":
                findings.append("一般护理记录重点字段：病情观察、护理措施、效果评价、下一班观察要点、一致性复核和关键字段。")
            if doc_type == "critical_patient_nursing_record":
                findings.append("病重护理记录重点字段：生命体征、出入量、病情观察、护理措施、效果评价、至少每4小时记录一次和下一班观察重点。")
            if doc_type == "glucose_record" and any(token in question for token in ("时间点", "餐前", "餐后", "随机血糖", "血酮体")):
                findings.append("建议重点核对并补录时间点、餐前/餐后、睡前、随机血糖及血酮体等扩展栏位。")
            if doc_type == "glucose_record" and any(token in question for token in ("监测顺序", "补录", "感染")):
                findings.append("留痕顺序建议：先补血糖客观数值，再补感染观察，再补医生沟通与交班提醒。")
            if doc_type == "glucose_record":
                findings.append("POCT重点字段：测量项目、血糖谱时间点、复测结果和下一班继续监测提醒。")
            recommendations = [
                {"title": "先人工审核草稿内容。", "priority": 1},
                {"title": "可直接点进患者档案修改字段和正文。", "priority": 1},
                {"title": "确认后在手机端提交并自动归档到对应患者档案。", "priority": 1},
            ]
            confidence = 0.84

        await self._write_audit(
            action="workflow.document",
            resource_id=patient_id,
            detail={"input": question, "document_type": doc_type, "bed_no": context.get("bed_no")},
            user_id=payload.requested_by,
        )
        resolved_bed_no = str(context.get("bed_no") or payload.bed_no or "").strip() or None
        resolved_patient_name = str(context.get("patient_name") or "").strip() or None
        return WorkflowOutput(
            workflow_type=WorkflowType.DOCUMENT,
            summary=summary,
            findings=findings,
            recommendations=recommendations,
            confidence=confidence,
            review_required=True,
            context_hit=True,
            patient_id=patient_id or None,
            patient_name=resolved_patient_name,
            bed_no=resolved_bed_no,
            steps=steps,
            created_at=datetime.now(timezone.utc),
        )


machine = AgentStateMachine()
