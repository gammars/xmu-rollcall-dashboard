import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "xmu-rollcall-cli"))
from xmu_rollcall.desktop_qt.app import DashboardWindow


class SessionRestoreTests(unittest.TestCase):
    def setUp(self):
        self.window = Mock()
        self.account = {"id": 2, "username": "test-user", "password": "test-password"}
        self.old, self.new = Mock(), Mock()
        self.mocks = {}
        for name, value in {
            "load_config": {}, "get_current_account": self.account,
            "get_cookies_path": "test-cookies.json", "requests.Session": self.old,
            "load_session": True, "verify_session": {"name": "Test"},
            "xmulogin": self.new, "save_session": None,
        }.items():
            patcher = patch(f"xmu_rollcall.desktop_qt.app.{name}", return_value=value)
            self.mocks[name] = patcher.start()
            self.addCleanup(patcher.stop)

    def restore(self):
        DashboardWindow._restore_worker(self.window, silent=True)

    def test_valid_cookie_skips_password(self):
        self.restore()
        self.mocks["xmulogin"].assert_not_called()
        self.window._emit.assert_called_once_with(("login_success", self.old, self.account))

    def test_expired_cookie_uses_password_and_saves_new_cookie(self):
        self.mocks["verify_session"].side_effect = [{}, {"name": "Test"}]
        self.restore()
        self.mocks["xmulogin"].assert_called_once_with(type=3, username="test-user", password="test-password")
        self.mocks["save_session"].assert_called_once_with(self.new, "test-cookies.json")
        self.window._emit.assert_called_with(("login_success", self.new, self.account))

    def test_missing_or_corrupt_cookie_uses_password(self):
        self.mocks["load_session"].return_value = False
        self.restore()
        self.mocks["verify_session"].assert_called_once_with(self.new)
        self.window._emit.assert_called_with(("login_success", self.new, self.account))

    def test_bad_password_fails_without_retry_or_save(self):
        self.mocks["verify_session"].return_value = {}
        self.mocks["xmulogin"].return_value = None
        self.restore()
        self.mocks["xmulogin"].assert_called_once()
        self.mocks["save_session"].assert_not_called()
        self.assertEqual(self.window._emit.call_args.args[0][0], "restore_failed")

    def test_new_session_must_pass_verification(self):
        self.mocks["verify_session"].return_value = {}
        self.restore()
        self.mocks["save_session"].assert_not_called()
        self.new.close.assert_called_once()
        self.assertEqual(self.window._emit.call_args.args[0][0], "restore_failed")

    def test_missing_password_fails(self):
        self.account.pop("password")
        self.mocks["load_session"].return_value = False
        self.restore()
        self.mocks["xmulogin"].assert_not_called()
        self.assertEqual(self.window._emit.call_args.args[0][0], "restore_failed")

    def test_login_exception_reports_failure_without_sensitive_details(self):
        self.mocks["load_session"].return_value = False
        self.mocks["xmulogin"].side_effect = RuntimeError("test-password")
        self.restore()
        event = self.window._emit.call_args.args[0]
        self.assertEqual(event[0], "restore_failed")
        self.assertNotIn("test-password", event[1])

    def test_startup_restores_without_requiring_cookie_file(self):
        self.window.session = None
        DashboardWindow.auto_restore_current_session(self.window)
        self.window._run_thread.assert_called_once_with(self.window._restore_worker, True)

    def test_failure_shows_logged_out_without_startup_popup(self):
        with patch("xmu_rollcall.desktop_qt.app.QMessageBox.critical") as dialog:
            DashboardWindow._handle_event(self.window, ("restore_failed", "失败", True))
        self.window._set_login_status.assert_called_once_with("未登录", warn=True)
        self.window.metric_account.setText.assert_called_once_with("未登录")
        dialog.assert_not_called()
