"""Build with an isolated DLL search path and smoke-test the actual EXE."""

import json
import os
from pathlib import Path
import subprocess
import sys


def main():
    root = Path(__file__).resolve().parents[1]
    prefix = Path(sys.prefix)
    windows = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    # Set this inside Python: launchers can re-inject PATH when spawning Python.
    os.environ["PATH"] = os.pathsep.join(map(str, (
        windows / "System32", windows, prefix, prefix / "Library" / "bin",
    )))
    os.environ.pop("QT_PLUGIN_PATH", None)
    os.environ.pop("QT_QPA_PLATFORM_PLUGIN_PATH", None)
    os.chdir(root)
    from PyInstaller.__main__ import run

    run([
        "--noconfirm", "--clean", "--windowed", "--onefile",
        "--name", "XMU-Rollcall-Dashboard", "--specpath", str(root / "build"),
        "--version-file", str(root / "scripts" / "windows_version_info.txt"),
        "--paths", str(root / "xmu-rollcall-cli"),
        "--collect-submodules", "xmu_rollcall", "--collect-submodules", "xmulogin",
        str(root / "scripts" / "xmu_dashboard_launcher.py"),
    ])
    exe = root / "dist" / "XMU-Rollcall-Dashboard.exe"
    report = root / "build" / "dashboard-smoke-test.json"
    report.unlink(missing_ok=True)
    result = subprocess.run(
        [str(exe), "--smoke-test", str(report)], timeout=60,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode or not report.exists():
        raise RuntimeError(f"EXE smoke test failed: exit={result.returncode}")
    data = json.loads(report.read_text(encoding="utf-8"))
    if not data.get("ok"):
        raise RuntimeError(f"EXE smoke test failed: {data}")
    print(f"EXE smoke test passed: {data}")


if __name__ == "__main__":
    main()
