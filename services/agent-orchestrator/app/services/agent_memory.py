from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.schemas.workflow import AgentMemorySnapshot, WorkflowOutput, WorkflowRequest, WorkflowType
from app.services.history_store import workflow_history_store


class AgentMemoryStore:
    def __init__(self) -> None:
        self._fp = Path(__file__).resolve().parents[2] / "data" / "agent_memory.json"
        self._st: dict[str, Any] = {
            "patients": {},
            "conversations": {},
            "users": {},
            "episodes": [],
        }
        self._load()

    def snapshot(
        self,
        *,
        patient_id: str | None = None,
        conversation_id: str | None = None,
        requested_by: str | None = None,
        user_input: str | None = None,
    ) -> AgentMemorySnapshot:
        pk = str(patient_id or "").strip()
        ck = str(conversation_id or "").strip()
        uk = str(requested_by or "").strip()

        patient_entry = self._st["patients"].get(pk, {}) if pk else {}
        conversation_entry = self._st["conversations"].get(ck, {}) if ck else {}
        user_entry = self._st["users"].get(uk, {}) if uk else {}

        limit = max(4, int(settings.agent_memory_recall_limit))
        history = workflow_history_store.list(
            patient_id=pk or None,
            conversation_id=ck or None,
            requested_by=uk or None,
            limit=limit,
        )
        episodes = self._match_episodes(
            patient_id=pk,
            conversation_id=ck,
            requested_by=uk,
            query=user_input,
            limit=max(4, limit),
        )

        summary_parts: list[str] = []

        def push_summary(value: str | None) -> None:
            cleaned = self._clean_summary(value)
            if cleaned and cleaned not in summary_parts:
                summary_parts.append(cleaned)

        push_summary(str(conversation_entry.get("conversation_summary") or ""))
        for episode in episodes:
            push_summary(str(episode.get("summary") or ""))
        for item in reversed(history):
            push_summary(item.summary)

        facts_raw: list[Any] = [patient_entry.get("patient_facts"), conversation_entry.get("patient_facts")]
        task_raw: list[Any] = [patient_entry.get("unresolved_tasks"), conversation_entry.get("unresolved_tasks")]
        action_raw: list[Any] = [conversation_entry.get("last_actions"), patient_entry.get("last_actions")]
        pref_raw: list[Any] = [user_entry.get("preferences"), conversation_entry.get("user_preferences")]

        for episode in episodes:
            facts_raw.extend(episode.get("facts", [])[:4])
            task_raw.extend(episode.get("tasks", [])[:4])
            action_raw.extend(episode.get("actions", [])[:3])
            pref_raw.extend(episode.get("preferences", [])[:2])

        for item in history:
            facts_raw.extend(item.findings[:3])
            task_raw.extend(
                [
                    str(recommendation.get("title") or "").strip()
                    for recommendation in item.recommendations[:3]
                    if isinstance(recommendation, dict)
                ]
            )
            action_raw.extend([artifact.title for artifact in item.artifacts[:3] if artifact.title.strip()])

        patient_facts = self._rank(user_input, self._merge(*facts_raw), limit=8)
        unresolved_tasks = self._rank(user_input, self._merge(*task_raw), limit=8)
        last_actions = self._rank(user_input, self._merge(*action_raw), limit=6)
        user_preferences = self._rank(user_input, self._merge(*pref_raw), limit=8)
        conversation_summary = "；".join(summary_parts[:3])[:420]

        return AgentMemorySnapshot(
            conversation_summary=conversation_summary,
            patient_facts=patient_facts,
            unresolved_tasks=unresolved_tasks,
            last_actions=last_actions,
            user_preferences=user_preferences,
        )

    def remember(self, req: WorkflowRequest, out: WorkflowOutput) -> AgentMemorySnapshot:
        patient_id = out.patient_id or req.patient_id
        snapshot = self.snapshot(
            patient_id=patient_id,
            conversation_id=req.conversation_id,
            requested_by=req.requested_by,
            user_input=req.user_input,
        )

        patient_key = str(patient_id or "").strip()
        conversation_key = str(req.conversation_id or "").strip()
        user_key = str(req.requested_by or "").strip()

        recommendation_titles = [
            str(item.get("title") or "").strip()
            for item in out.recommendations
            if isinstance(item, dict) and str(item.get("title") or "").strip()
        ]
        artifact_titles = [item.title for item in out.artifacts if item.title.strip()]
        approval_titles = [item.title for item in out.pending_approvals if item.title.strip()]
        preferences = self._get_prefs(req)

        if patient_key:
            self._st["patients"][patient_key] = {
                "patient_facts": self._merge(snapshot.patient_facts, out.findings, self._get_facts(out))[:12],
                "unresolved_tasks": self._merge(snapshot.unresolved_tasks, recommendation_titles, out.next_actions, approval_titles)[:12],
                "last_actions": self._merge(snapshot.last_actions, artifact_titles)[:8],
            }

        if conversation_key:
            self._st["conversations"][conversation_key] = {
                "conversation_summary": self._summary_for_store(out)[:320],
                "patient_facts": self._merge(snapshot.patient_facts, out.findings)[:12],
                "unresolved_tasks": self._merge(snapshot.unresolved_tasks, recommendation_titles, out.next_actions, approval_titles)[:12],
                "last_actions": self._merge(snapshot.last_actions, artifact_titles)[:8],
                "user_preferences": preferences,
            }

        if user_key:
            old = self._st["users"].get(user_key, {})
            self._st["users"][user_key] = {
                "preferences": self._merge(old.get("preferences"), snapshot.user_preferences, preferences)[:8]
            }

        self._remember_episode(req, out, preferences)
        self._save()
        return self.snapshot(
            patient_id=patient_id,
            conversation_id=req.conversation_id,
            requested_by=req.requested_by,
            user_input=req.user_input,
        )

    def _remember_episode(self, req: WorkflowRequest, out: WorkflowOutput, preferences: list[str]) -> None:
        episode = {
            "patient_id": str(out.patient_id or req.patient_id or "").strip(),
            "conversation_id": str(req.conversation_id or "").strip(),
            "requested_by": str(req.requested_by or "").strip(),
            "workflow_type": str(out.workflow_type.value if isinstance(out.workflow_type, WorkflowType) else out.workflow_type),
            "summary": self._summary_for_store(out),
            "facts": self._merge(self._get_facts(out), out.findings)[:8],
            "tasks": self._merge(
                [
                    str(item.get("title") or "").strip()
                    for item in out.recommendations
                    if isinstance(item, dict)
                ],
                out.next_actions,
                [item.title for item in out.pending_approvals],
            )[:8],
            "actions": self._merge(
                [item.title for item in out.artifacts],
                [
                    str(getattr(step, "note", "") or getattr(step, "agent", "") or "").strip()
                    for step in out.steps
                ],
            )[:6],
            "preferences": preferences[:4],
            "keywords": self._episode_keywords(req, out),
            "focus_tags": self._extract_focus_tags(req, out),
            "bed_refs": self._extract_bed_refs(req, out),
            "artifact_kinds": [str(item.kind or "").strip() for item in out.artifacts if str(item.kind or "").strip()],
            "created_at": str(out.created_at or ""),
        }
        episodes = self._st.get("episodes")
        if not isinstance(episodes, list):
            episodes = []
        episodes.append(episode)
        self._st["episodes"] = episodes[-500:]

    def _match_episodes(
        self,
        *,
        patient_id: str,
        conversation_id: str,
        requested_by: str,
        query: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        episodes = self._st.get("episodes")
        if not isinstance(episodes, list):
            return []

        query_keywords = set(self._kw(query))
        query_beds = set(self._extract_bed_refs_from_text(query))
        scored: list[tuple[int, int, dict[str, Any]]] = []
        for index, raw_episode in enumerate(episodes):
            if not isinstance(raw_episode, dict):
                continue
            score = 0
            if patient_id and str(raw_episode.get("patient_id") or "").strip() == patient_id:
                score += 5
            if conversation_id and str(raw_episode.get("conversation_id") or "").strip() == conversation_id:
                score += 4
            if requested_by and str(raw_episode.get("requested_by") or "").strip() == requested_by:
                score += 2
            episode_keywords = {str(item).strip().lower() for item in raw_episode.get("keywords", []) if str(item).strip()}
            focus_tags = {str(item).strip().lower() for item in raw_episode.get("focus_tags", []) if str(item).strip()}
            episode_beds = {str(item).strip().lower() for item in raw_episode.get("bed_refs", []) if str(item).strip()}
            score += len(query_keywords & episode_keywords) * 2
            score += len(query_keywords & focus_tags) * 3
            score += len(query_beds & episode_beds) * 4
            score += self._recency_bonus(str(raw_episode.get("created_at") or ""))
            if score <= 0:
                continue
            scored.append((score, index, raw_episode))

        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [item[2] for item in scored[:limit]]

    def _episode_keywords(self, req: WorkflowRequest, out: WorkflowOutput) -> list[str]:
        parts = [
            str(req.user_input or ""),
            str(req.mission_title or ""),
            str(out.summary or ""),
            str(out.patient_name or ""),
            str(out.bed_no or req.bed_no or ""),
        ]
        parts.extend(str(item.title or "") for item in out.artifacts[:4])
        parts.extend(str(item.get("title") or "") for item in out.recommendations[:4] if isinstance(item, dict))
        return self._kw(" ".join(parts))[:18]

    @classmethod
    def _extract_focus_tags(cls, req: WorkflowRequest, out: WorkflowOutput) -> list[str]:
        parts = [
            str(req.user_input or ""),
            str(req.mission_title or ""),
            str(out.summary or ""),
            str(out.patient_name or ""),
            str(out.bed_no or req.bed_no or ""),
            str(out.workflow_type.value if isinstance(out.workflow_type, WorkflowType) else out.workflow_type or ""),
        ]
        parts.extend(str(item.title or "") for item in out.artifacts[:4])
        parts.extend(str(item.get("title") or "") for item in out.recommendations[:4] if isinstance(item, dict))
        parts.extend(str(item or "") for item in out.next_actions[:4])
        return cls._kw(" ".join(parts))[:24]

    @classmethod
    def _extract_bed_refs(cls, req: WorkflowRequest, out: WorkflowOutput) -> list[str]:
        refs = cls._extract_bed_refs_from_text(req.user_input)
        refs.extend(cls._extract_bed_refs_from_text(req.mission_title))
        refs.extend(cls._extract_bed_refs_from_text(out.summary))
        refs.extend(cls._extract_bed_refs_from_text(out.patient_name))
        refs.extend(cls._extract_bed_refs_from_text(out.bed_no or req.bed_no))
        refs.extend(cls._extract_bed_refs_from_text(" ".join(out.findings[:4])))
        return cls._merge(refs)[:8]

    @staticmethod
    def _extract_bed_refs_from_text(text: str | None) -> list[str]:
        raw = str(text or "").strip()
        if not raw:
            return []

        refs: list[str] = []
        for pattern in (r"([A-Za-z]?\d{1,3}[A-Za-z]?)\s*床", r"\bbed\s*([A-Za-z]?\d{1,3}[A-Za-z]?)\b"):
            refs.extend(re.findall(pattern, raw, flags=re.IGNORECASE))
        if re.fullmatch(r"[A-Za-z]?\d{1,3}[A-Za-z]?", raw):
            refs.append(raw)
        return [str(item).strip().lower() for item in refs if str(item).strip()]

    @staticmethod
    def _recency_bonus(created_at: str | None) -> int:
        raw = str(created_at or "").strip()
        if not raw:
            return 0
        try:
            normalized = raw.replace("Z", "+00:00")
            created = datetime.fromisoformat(normalized)
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            age_sec = max(0.0, (datetime.now(timezone.utc) - created.astimezone(timezone.utc)).total_seconds())
        except Exception:
            return 0
        if age_sec <= 6 * 3600:
            return 3
        if age_sec <= 24 * 3600:
            return 2
        if age_sec <= 72 * 3600:
            return 1
        return 0

    @staticmethod
    def _clean_summary(text: str | None) -> str:
        raw = str(text or "").strip()
        if not raw:
            return ""
        blocked_tokens = (
            "自动闭环已完成初步分析",
            "文书草稿已生成",
            "已生成病区交接草稿",
            "草稿ID:",
            "交班ID:",
        )
        if any(token in raw for token in blocked_tokens):
            return ""
        return raw[:180]

    def _summary_for_store(self, out: WorkflowOutput) -> str:
        summary = self._clean_summary(out.summary)
        if out.workflow_type == WorkflowType.AUTONOMOUS_CARE:
            concise = self._merge(
                out.findings[:2],
                [
                    str(item.get("title") or "").strip()
                    for item in out.recommendations[:2]
                    if isinstance(item, dict)
                ],
                out.next_actions[:1],
            )
            summary = "；".join([item for item in concise[:2] if item])[:200]
        return summary[:320]

    @staticmethod
    def _merge(*args: Any) -> list[str]:
        output: list[str] = []
        seen: set[str] = set()
        for group in args:
            if not group:
                continue
            items = group if isinstance(group, list) else [group]
            for item in items:
                text = str(item or "").strip()
                if not text or text in seen:
                    continue
                seen.add(text)
                output.append(text)
        return output

    @classmethod
    def _kw(cls, text: str | None) -> list[str]:
        raw = str(text or "").strip().lower()
        if not raw:
            return []

        output: list[str] = []
        seen: set[str] = set()
        tokens = re.findall(r"[a-z0-9_-]+|[\u4e00-\u9fff]+", raw)
        for token in tokens:
            has_digit = any(ch.isdigit() for ch in token)
            if len(token) <= 1 and not has_digit:
                continue
            fragments = [token]
            if len(token) > 4 and re.search(r"[\u4e00-\u9fff]", token):
                fragments = [token[idx : idx + 2] for idx in range(0, len(token) - 1)]
            for fragment in fragments:
                has_digit = any(ch.isdigit() for ch in fragment)
                if len(fragment) <= 1 and not has_digit:
                    continue
                if fragment in seen:
                    continue
                seen.add(fragment)
                output.append(fragment)
        return output

    @classmethod
    def _rank(cls, query: str | None, items: list[str], *, limit: int) -> list[str]:
        if not items:
            return []
        keywords = cls._kw(query)
        if not keywords:
            return items[:limit]

        scored: list[tuple[int, int, str]] = []
        for idx, item in enumerate(items):
            haystack = str(item or "").strip().lower()
            score = 0
            for keyword in keywords:
                if keyword not in haystack:
                    continue
                score += 3 if any(ch.isdigit() for ch in keyword) else 1
            scored.append((score, -idx, item))
        scored.sort(reverse=True)
        return [value for _, _, value in scored[:limit]]

    @staticmethod
    def _get_prefs(req: WorkflowRequest) -> list[str]:
        query = str(req.user_input or "").strip()
        prefs: list[str] = []
        if any(token in query for token in ("自动", "闭环", "持续跟进")):
            prefs.append("偏好自动闭环处理")
        if any(token in query for token in ("文书", "记录", "草稿", "表格")):
            prefs.append("偏好结构化文书草稿")
        if "交班" in query or "交接班" in query:
            prefs.append("偏好交接班联动")
        if any(token in query for token in ("通知", "协作", "提醒医生")):
            prefs.append("偏好主动协作提醒")
        if any(token in query for token in ("中医", "辨证", "证候")):
            prefs.append("偏好中医护理补充")
        return prefs

    @staticmethod
    def _get_facts(out: WorkflowOutput) -> list[str]:
        items: list[str] = []
        if out.patient_name and out.bed_no:
            items.append(f"{out.bed_no}床 {out.patient_name}")
        items.extend([str(finding or "").strip() for finding in out.findings[:6] if str(finding or "").strip()])
        return items

    def _load(self) -> None:
        if not self._fp.exists():
            return
        try:
            payload = json.loads(self._fp.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                for key in ("patients", "conversations", "users"):
                    value = payload.get(key)
                    if isinstance(value, dict):
                        self._st[key] = value
                episodes = payload.get("episodes")
                if isinstance(episodes, list):
                    self._st["episodes"] = episodes
        except Exception:
            self._st = {"patients": {}, "conversations": {}, "users": {}, "episodes": []}

    def _save(self) -> None:
        self._fp.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "patients": self._st.get("patients", {}),
            "conversations": self._st.get("conversations", {}),
            "users": self._st.get("users", {}),
            "episodes": list(self._st.get("episodes", []))[-500:],
        }
        self._fp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


agent_memory_store = AgentMemoryStore()
