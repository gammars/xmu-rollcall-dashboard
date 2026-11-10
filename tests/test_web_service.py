import unittest
from unittest.mock import patch

from xmu_rollcall.desktop_qt.core import RollcallSnapshot
from xmu_rollcall.web.app import app
from xmu_rollcall.web.service import WebService


def make_snapshot(rollcall_id="r1", rollcall_type="数字签到", signed=1, total=4):
    return RollcallSnapshot(
        rollcall_id=rollcall_id,
        course_id="course-1",
        course_title="软件工程",
        rollcall_time="2026-11-10T01:00:00Z",
        utc_date="2026-11-10",
        end_time="",
        status="active",
        rollcall_type=rollcall_type,
        number_code="0026" if rollcall_type == "数字签到" else "",
        signed_count=signed,
        total_count=total,
        student_rollcalls=[],
        raw={},
    )


class WebServiceTests(unittest.TestCase):
    def make_service(self):
        service = WebService()
        service.session = object()
        service.account = {
            "id": 1,
            "username": "test",
            "rollcall_settings": {
                "attendance_threshold_enabled": True,
                "attendance_threshold_percent": 10,
                "course_location_bindings": {},
            },
        }
        return service

    def test_fastapi_routes_exist(self):
        paths = {route.path for route in app.routes}
        self.assertIn("/api/health", paths)
        self.assertIn("/api/login", paths)
        self.assertIn("/api/monitor/start", paths)
        self.assertIn("/api/location-binding", paths)

    def test_number_rollcall_waits_until_auto_submit_is_enabled(self):
        service = self.make_service()
        row = {"rollcall_id": "r1", "course_title": "软件工程", "is_number": True}
        snapshot = make_snapshot()
        with (
            patch("xmu_rollcall.web.service.fetch_rollcall_snapshot", return_value=snapshot),
            patch.object(service, "_location_for_event", return_value=None),
            patch("xmu_rollcall.web.service.send_code", return_value=True) as send,
        ):
            service._register_event(row)
            self.assertEqual(service.events["r1"]["number_code"], "0026")
            self.assertEqual(service.events["r1"]["attendance"], "1/4")
            self.assertEqual(service.events["r1"]["result"], "可提交")
            send.assert_not_called()

            service.auto_submit = True
            service._refresh_event("r1")
            service._refresh_event("r1")
            send.assert_called_once_with(service.session, "r1", "0026")
            self.assertEqual(service.events["r1"]["result"], "已签到")

    def test_other_rollcall_still_displays_attendance(self):
        service = self.make_service()
        row = {"rollcall_id": "r2", "course_title": "软件工程"}
        with patch(
            "xmu_rollcall.web.service.fetch_rollcall_snapshot",
            return_value=make_snapshot("r2", "签到", signed=2, total=4),
        ):
            service._register_event(row)
        self.assertEqual(service.events["r2"]["attendance"], "2/4")
        self.assertEqual(service.events["r2"]["strategy_status"], "仅提醒")
        self.assertNotIn("r2", service.pending_ids)


if __name__ == "__main__":
    unittest.main()
