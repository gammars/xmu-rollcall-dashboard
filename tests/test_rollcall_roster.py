from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "xmu-rollcall-cli"))

from xmu_rollcall.config import normalize_rollcall_settings  # noqa: E402
from xmu_rollcall.desktop_qt.core import (  # noqa: E402
    fetch_rollcall_roster,
    parse_rollcall_roster,
)


class RollcallRosterTests(unittest.TestCase):
    def test_parse_roster_normalizes_fields(self):
        roster = parse_rollcall_roster("42", {
            "title": "课堂签到",
            "status": "started",
            "student_rollcalls": [
                {
                    "name": "测试同学",
                    "user_no": "10001",
                    "klass": {"name": "软件一班"},
                    "department": {"name": "信息学院"},
                    "rollcall_status": "on_call_fine",
                    "updated_at": "2026-09-11T01:02:03Z",
                    "distance": 12.5,
                    "status_detail": "正常",
                },
                {
                    "name": "未签同学",
                    "user_no": "10002",
                    "rollcall_status": "absent",
                },
            ],
        })

        self.assertEqual(roster.rollcall_id, "42")
        self.assertEqual(roster.status, "started")
        self.assertEqual(len(roster.students), 2)
        self.assertEqual(roster.students[0].klass, "软件一班")
        self.assertEqual(roster.students[0].department, "信息学院")
        self.assertEqual(roster.students[0].rollcall_status, "已签到")
        self.assertEqual(roster.students[0].rollcall_time, "2026-09-11 09:02:03")
        self.assertEqual(roster.students[0].distance, "12.5 米")
        self.assertEqual(roster.students[1].rollcall_status, "未签到")
        self.assertEqual(roster.students[1].rollcall_time, "-")

    def test_fetch_roster_uses_detail_endpoint(self):
        class Response:
            status_code = 200

            def raise_for_status(self):
                return None

            def json(self):
                return {"student_rollcalls": [{"name": "测试同学", "rollcall_status": "on_call"}]}

        class Session:
            def __init__(self):
                self.urls = []

            def get(self, url, **_kwargs):
                self.urls.append(url)
                return Response()

        session = Session()
        roster = fetch_rollcall_roster(session, "307164")
        self.assertEqual(roster.rollcall_id, "307164")
        self.assertTrue(session.urls[0].endswith("/api/rollcall/307164/student_rollcalls"))

    def test_threshold_defaults_and_bounds(self):
        defaults = normalize_rollcall_settings(None)
        self.assertTrue(defaults["attendance_threshold_enabled"])
        self.assertEqual(defaults["attendance_threshold_percent"], 10)
        self.assertEqual(
            normalize_rollcall_settings({"attendance_threshold_percent": 0})[
                "attendance_threshold_percent"
            ],
            1,
        )
        self.assertEqual(
            normalize_rollcall_settings({"attendance_threshold_percent": 200})[
                "attendance_threshold_percent"
            ],
            100,
        )


if __name__ == "__main__":
    unittest.main()
