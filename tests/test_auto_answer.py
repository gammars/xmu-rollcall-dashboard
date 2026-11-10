from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "xmu-rollcall-cli"))

from PySide6.QtWidgets import QApplication
from xmu_rollcall.desktop_qt.app import DashboardWindow
from xmu_rollcall.desktop_qt.core import RollcallEvent, RollcallSnapshot


class AutoAnswerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        with patch.object(DashboardWindow, "_load_current_account_hint"), patch(
            "xmu_rollcall.desktop_qt.app.QTimer.singleShot"
        ):
            self.window = DashboardWindow()
        self.window.runtime_timer.stop()
        self.window.strategy_timer.stop()
        self.window.session = object()
        self.window.account = {"id": 1}
        self.threads = patch.object(self.window, "_run_thread").start()
        patch.object(self.window, "start_monitor").start()
        self.messages = patch("xmu_rollcall.desktop_qt.app.QMessageBox.information").start()
        self.event = RollcallEvent("42", "测试课程", "教师", "数字签到", "started", {}, number_code="0026", course_id="9")
        self.window.events_by_id["e"] = self.event
        self.window.event_order.append("e")
        self.window.strategy_pending_ids.add("e")

    def tearDown(self):
        patch.stopall()
        self.window.deleteLater()

    def roster(self, signed=1, total=10, status="started"):
        return RollcallSnapshot(
            "42", "9", "测试", "2026-10-19T00:00:00Z", "2026-10-19",
            "2099-10-19T01:00:00Z", status, self.event.rollcall_type, "0026",
            signed, total, [], {},
        )

    def enable(self):
        self.window.auto_answer_check.setChecked(True)
        self.threads.reset_mock()

    def test_exact_threshold_submits_once_and_manual_click_cannot_duplicate(self):
        self.enable()
        self.window._handle_strategy_check("e", self.roster())
        self.window._handle_strategy_check("e", self.roster())
        self.window._answer_event("e", self.event)
        self.assertEqual(self.event.result, "处理中")
        self.threads.assert_called_once_with(
            self.window._answer_worker, "e", self.event, self.window.session
        )
        self.messages.assert_not_called()

    def test_below_threshold_and_empty_roster_wait(self):
        self.enable()
        for roster in (self.roster(0), self.roster(0, 0)):
            self.window._handle_strategy_check("e", roster)
            self.assertEqual(self.event.result, "等待阈值" if roster.total_count else "等待数据")
        self.threads.assert_not_called()

    def test_ended_rollcall_never_submits_even_above_threshold(self):
        self.enable()
        self.window._handle_strategy_check("e", self.roster(status="finished"))
        self.assertEqual(self.event.result, "已结束")
        self.threads.assert_not_called()

    def test_turning_off_during_check_only_reminds(self):
        self.enable()
        self.window.auto_answer_check.setChecked(False)
        self.window._handle_strategy_check("e", self.roster())
        self.assertEqual(self.event.result, "可处理")
        self.threads.assert_not_called()
        self.messages.assert_called_once()

    def test_qr_requires_manual_without_blocking_dialog(self):
        self.enable()
        self.event.rollcall_type = "二维码签到"
        self.window._handle_strategy_check("e", self.roster())
        self.assertEqual(self.event.result, "需手动")
        self.threads.assert_not_called()
        self.messages.assert_not_called()

    def test_radar_without_binding_does_not_submit(self):
        self.enable()
        self.event.rollcall_type = "雷达签到"
        with patch.object(self.window, "_location_for_event", return_value=None):
            self.window._handle_strategy_check("e", self.roster())
        self.assertEqual(self.event.result, "未配置地点")
        self.threads.assert_not_called()

    def test_radar_with_binding_submits_at_shared_threshold(self):
        self.enable()
        self.event.rollcall_type = "雷达签到"
        preset = {"name": "教室", "latitude": 24.0, "longitude": 118.0, "accuracy": 35}
        with patch.object(self.window, "_location_for_event", return_value=preset):
            self.window._handle_strategy_check("e", self.roster())
        self.threads.assert_called_once_with(
            self.window._answer_worker, "e", self.event, self.window.session
        )

    def test_stop_disables_auto_answer(self):
        self.enable()
        self.window.stop_monitor()
        self.assertFalse(self.window.auto_answer_check.isChecked())
        self.assertTrue(self.window.attendance_threshold_enabled_check.isEnabled())

    def test_custom_threshold_is_used(self):
        self.enable()
        self.window.attendance_threshold_spin.setValue(20)
        self.window._handle_strategy_check("e", self.roster(1))
        self.threads.assert_not_called()
        self.window._handle_strategy_check("e", self.roster(2))
        self.assertEqual(self.threads.call_count, 1)

    def test_disabled_threshold_ignores_inflight_response(self):
        self.window.attendance_threshold_enabled_check.setChecked(False)
        self.window._handle_strategy_check("e", self.roster())
        self.threads.assert_not_called()
        self.messages.assert_not_called()

    def test_worker_submits_using_captured_session(self):
        session = self.window.session
        self.window.session = None
        with patch("xmu_rollcall.desktop_qt.app.send_code", return_value=True) as send, patch.object(
            self.window, "_emit"
        ) as emit:
            self.window._answer_worker("e", self.event, session)
        send.assert_called_once_with(session, "42", "0026")
        emit.assert_called_once_with(("answer_result", "e", True, "提交成功"))

    def test_failed_submit_is_not_automatically_retried(self):
        self.enable()
        self.window._handle_strategy_check("e", self.roster())
        self.window._handle_event(("answer_result", "e", False, "提交失败"))
        self.window._handle_strategy_check("e", self.roster())
        self.assertEqual(self.event.result, "失败")
        self.assertEqual(self.threads.call_count, 1)


if __name__ == "__main__":
    unittest.main()
