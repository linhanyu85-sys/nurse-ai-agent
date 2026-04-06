from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.agent_memory import AgentMemoryStore  # noqa: E402


class AgentMemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = AgentMemoryStore()
        self.store._st = {
            "patients": {},
            "conversations": {},
            "users": {},
            "episodes": [],
        }

    def test_recency_bonus_handles_recent_timestamp(self) -> None:
        score = self.store._recency_bonus(datetime.now(timezone.utc).isoformat())
        self.assertGreaterEqual(score, 1)

    def test_match_episodes_prefers_same_bed_reference(self) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.store._st["episodes"] = [
            {
                "patient_id": "pat-012",
                "conversation_id": "conv-1",
                "requested_by": "u-1",
                "summary": "12床低血压与少尿需继续复评。",
                "facts": ["12床低血压"],
                "tasks": ["复测血压"],
                "actions": ["准备交接摘要"],
                "preferences": [],
                "keywords": ["12床", "低血压", "复评"],
                "focus_tags": ["12床", "少尿"],
                "bed_refs": ["12"],
                "created_at": now,
            },
            {
                "patient_id": "pat-023",
                "conversation_id": "conv-2",
                "requested_by": "u-1",
                "summary": "23床输血观察需记录。",
                "facts": ["23床输血"],
                "tasks": ["双人核对"],
                "actions": ["准备输血记录"],
                "preferences": [],
                "keywords": ["23床", "输血"],
                "focus_tags": ["23床", "输血"],
                "bed_refs": ["23"],
                "created_at": now,
            },
        ]

        matches = self.store._match_episodes(
            patient_id="",
            conversation_id="",
            requested_by="u-1",
            query="请继续跟进12床低血压并补交接摘要",
            limit=2,
        )

        self.assertEqual(len(matches), 2)
        self.assertIn("12床", matches[0]["summary"])


if __name__ == "__main__":
    unittest.main()
