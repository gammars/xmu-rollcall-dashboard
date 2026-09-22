"""PyInstaller launcher for the XMU Rollcall PySide6 desktop dashboard."""

import sys


def smoke_test(report_path):
    import json
    from pathlib import Path
    import traceback

    try:
        from PySide6.QtCore import qVersion
        from PySide6.QtWidgets import QApplication
        from xmu_rollcall.desktop_qt.app import DashboardWindow

        # Exercise real widgets without restoring credentials or making requests.
        DashboardWindow._load_current_account_hint = lambda self: None
        DashboardWindow.auto_restore_current_session = lambda self: None
        app = QApplication([])
        window = DashboardWindow()
        window.show()
        app.processEvents()
        assert window.auto_answer_check.text() == "开启自动签到（谨慎）"
        assert not window.auto_answer_check.isChecked()
        data = {"ok": True, "qt": qVersion(), "button": window.auto_answer_check.text()}
        window.close()
        app.processEvents()
    except Exception:
        data = {"ok": False, "error": traceback.format_exc()}
    Path(report_path).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return 0 if data["ok"] else 1


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--smoke-test":
        sys.exit(smoke_test(sys.argv[2]))
    from xmu_rollcall.desktop_qt.app import main

    main()
