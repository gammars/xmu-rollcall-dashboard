diff --git a/D:\大学内容\大三下\项目分析\厦门大学自动签到\xmu-rollcall-bot\scripts\_probe_timetable_rollcall.py b/D:\大学内容\大三下\项目分析\厦门大学自动签到\xmu-rollcall-bot\scripts\_probe_timetable_rollcall.py
new file mode 100644
--- /dev/null
+++ b/D:\大学内容\大三下\项目分析\厦门大学自动签到\xmu-rollcall-bot\scripts\_probe_timetable_rollcall.py
@@ -0,0 +1,188 @@
+"""Temporary read-only probe for the timetable_rollcalls endpoint."""
+
+from __future__ import annotations
+
+import contextlib
+import io
+import json
+import os
+import sys
+from datetime import datetime, timezone
+from pathlib import Path
+
+import requests
+
+
+ROOT = Path(__file__).resolve().parents[1]
+sys.path.insert(0, str(ROOT / "xmu-rollcall-cli"))
+
+from xmu_rollcall.config import get_cookies_path, get_current_account, load_config  # noqa: E402
+from xmu_rollcall.desktop_qt.core import (  # noqa: E402
+    COURSE_ENDPOINTS,
+    ROLLCALL_ENDPOINT_TEMPLATES,
+    fetch_first_json,
+    first_value,
+    get_profile_user_id,
+    normalize_course,
+    normalize_rollcall_type,
+    unwrap_list,
+)
+from xmu_rollcall.utils import base_url, headers, load_session, save_session, verify_session  # noqa: E402
+
+
+def establish_session():
+    account = get_current_account(load_config())
+    if not account:
+        raise RuntimeError("no_saved_account")
+    session = requests.Session()
+    cookie_path = get_cookies_path(account.get("id", 1))
+    if load_session(session, cookie_path) and verify_session(session):
+        return session, "saved_cookie"
+    session.close()
+
+    username = str(account.get("username") or "")
+    password = str(account.get("password") or "")
+    if not username or not password:
+        raise RuntimeError("no_valid_session_or_credentials")
+    from xmulogin import xmulogin
+
+    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
+        session = xmulogin(type=3, username=username, password=password)
+    if not session or not verify_session(session):
+        raise RuntimeError("relogin_failed")
+    save_session(session, cookie_path)
+    return session, "password_relogin"
+
+
+def parse_stamp(value):
+    text = str(value or "").strip()
+    if not text:
+        return datetime.min.replace(tzinfo=timezone.utc)
+    try:
+        stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
+        if stamp.tzinfo is None:
+            stamp = stamp.replace(tzinfo=timezone.utc)
+        return stamp.astimezone(timezone.utc)
+    except ValueError:
+        return datetime.min.replace(tzinfo=timezone.utc)
+
+
+def main():
+    session, auth_source = establish_session()
+    profile_id = get_profile_user_id(session)
+    courses_payload, _ = fetch_first_json(session, COURSE_ENDPOINTS, "courses")
+    raw_courses = unwrap_list(courses_payload, ("courses", "data", "items", "list", "results"))
+    matches = []
+    for raw in raw_courses:
+        course = normalize_course(raw)
+        if course and "软件工程" in course["title"]:
+            matches.append(course)
+
+    candidates = []
+    course_errors = 0
+    for course in matches:
+        endpoints = tuple(
+            template.format(course_id=course["id"], student_id=profile_id)
+            for template in ROLLCALL_ENDPOINT_TEMPLATES
+        )
+        try:
+            payload, _ = fetch_first_json(session, endpoints, "rollcalls")
+        except Exception:
+            course_errors += 1
+            continue
+        rollcalls = unwrap_list(payload, ("rollcalls", "activities", "data", "items", "list", "results"))
+        for row in rollcalls:
+            if not isinstance(row, dict) or normalize_rollcall_type(row) != "数字签到":
+                continue
+            rollcall_id = str(first_value(row, ("rollcall_id", "id", "activity_id"), ""))
+            stamp_value = first_value(row, ("rollcall_time", "created_at", "start_time", "time"), "")
+            if rollcall_id:
+                candidates.append((parse_stamp(stamp_value), rollcall_id, course))
+
+    if not candidates:
+        print(json.dumps({
+            "ok": False,
+            "stage": "locate_rollcall",
+            "auth": auth_source,
+            "matching_courses": len(matches),
+            "course_query_errors": course_errors,
+            "error": "no_number_rollcall_found",
+        }, ensure_ascii=False))
+        return 2
+
+    candidates.sort(key=lambda item: item[0], reverse=True)
+    history_stamp, history_id, course = candidates[0]
+
+    detail_response = session.get(
+        f"{base_url}/api/rollcall/{history_id}", headers=headers, timeout=20
+    )
+    detail_json = detail_response.json() if detail_response.status_code == 200 else None
+    if not isinstance(detail_json, dict):
+        print(json.dumps({
+            "ok": False,
+            "stage": "rollcall_detail",
+            "auth": auth_source,
+            "status": detail_response.status_code,
+        }, ensure_ascii=False))
+        return 3
+
+    detail_course_id = first_value(detail_json, ("course_id",), "")
+    detail_time = first_value(detail_json, ("rollcall_time",), "")
+    if not detail_course_id or not detail_time:
+        print(json.dumps({
+            "ok": False,
+            "stage": "rollcall_detail_fields",
+            "auth": auth_source,
+            "has_course_id": bool(detail_course_id),
+            "has_rollcall_time": bool(detail_time),
+        }, ensure_ascii=False))
+        return 4
+
+    utc_stamp = parse_stamp(detail_time)
+    rollcall_date = utc_stamp.date().isoformat()
+    timetable_response = session.get(
+        f"{base_url}/api/timetable_rollcalls",
+        params={"course_ids": detail_course_id, "rollcall_date": rollcall_date},
+        headers={**headers, "Accept": "application/json, text/plain, */*", "Referer": f"{base_url}/"},
+        timeout=20,
+    )
+    try:
+        timetable_json = timetable_response.json()
+    except ValueError:
+        timetable_json = None
+
+    rows = timetable_json.get("rollcalls") if isinstance(timetable_json, dict) else None
+    target = None
+    if isinstance(rows, list):
+        target = next(
+            (row for row in rows if isinstance(row, dict) and str(row.get("id")) == history_id),
+            None,
+        )
+    code = target.get("number_code") if isinstance(target, dict) else None
+    code_text = str(code).strip() if code is not None else ""
+    status = target.get("status") if isinstance(target, dict) else None
+    end_time = target.get("end_time") if isinstance(target, dict) else None
+
+    print(json.dumps({
+        "ok": True,
+        "auth": auth_source,
+        "matching_courses": len(matches),
+        "course_query_errors": course_errors,
+        "number_rollcall_candidates": len(candidates),
+        "selected_course_title": course["title"],
+        "selected_rollcall_time_utc": utc_stamp.isoformat(),
+        "detail_http_status": detail_response.status_code,
+        "timetable_http_status": timetable_response.status_code,
+        "timetable_rollcall_rows": len(rows) if isinstance(rows, list) else None,
+        "target_rollcall_found": target is not None,
+        "number_code_present": bool(code_text),
+        "number_code_is_numeric": code_text.isdigit(),
+        "number_code_length": len(code_text),
+        "rollcall_status": status,
+        "end_time": end_time,
+    }, ensure_ascii=False))
+    return 0
+
+
+if __name__ == "__main__":
+    raise SystemExit(main())
