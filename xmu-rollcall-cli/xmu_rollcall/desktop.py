"""Tkinter desktop dashboard for XMU Rollcall Bot.

The desktop app intentionally keeps answer execution user-controlled by
default. It reuses the existing login, polling and submit helpers while moving
long-running work onto background threads so the UI stays responsive.
"""

from __future__ import annotations

import queue
import threading
import time
import tkinter as tk
import os
import webbrowser
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import requests
from xmulogin import xmulogin

from . import __version__
from .config import (
    add_account,
    get_all_accounts,
    get_cookies_path,
    get_current_account,
    get_rollcall_settings,
    load_config,
    save_config,
    set_current_account,
    set_rollcall_settings,
)
from .courseware import (
    CourseSummary,
    CoursewareItem,
    download_courseware,
    fetch_courses,
    fetch_courseware,
    format_size,
    sanitize_filename,
)
from .jw_grades import GradeRecord, fetch_grade_records
from .proxy_guard import disable_system_proxies
from .utils import base_url, headers, load_session, retry_request, save_session, verify_session
from .verify import find_number_code, send_code, send_radar


disable_system_proxies()


ROLLCALLS_URL = f"{base_url}/api/radar/rollcalls"
DEFAULT_POLL_INTERVAL_SECONDS = 1

COURSE_ENDPOINTS = (
    "/api/my-courses?per_page=1000",
    "/api/courses?role=student&per_page=1000",
    "/api/courses?course_role=student&per_page=1000",
    "/api/my/courses?per_page=1000",
    "/api/courses?per_page=1000",
    "/api/courses",
)

ROLLCALL_ENDPOINT_TEMPLATES = (
    "/api/course/{course_id}/student/{student_id}/rollcalls?page=1&page_size=100",
    "/api/course/{course_id}/student/{student_id}/rollcalls",
)


@dataclass
class RollcallEvent:
    rollcall_id: str
    course_title: str
    teacher: str
    rollcall_type: str
    status: str
    raw: dict
    detected_at: float = field(default_factory=time.time)
    result: str = "待处理"
    detail: str = ""
    number_code: str = ""


@dataclass
class CourseRollcallRecord:
    course_id: str
    course_title: str
    rollcall_id: str
    rollcall_time: str
    rollcall_type: str
    signed_status: str
    platform_status: str
    detail: str = ""


class MonitorWorker(threading.Thread):
    def __init__(self, session, account, outbox: queue.Queue, stop_event: threading.Event, interval: int):
        super().__init__(daemon=True)
        self.session = session
        self.account = account
        self.outbox = outbox
        self.stop_event = stop_event
        self.interval = max(1, int(interval or DEFAULT_POLL_INTERVAL_SECONDS))
        self.query_count = 0
        self.seen_rollcall_ids: set[str] = set()
        self.last_payload = {"rollcalls": []}

    def run(self):
        self.outbox.put(("monitor_status", "运行中"))
        while not self.stop_event.is_set():
            try:
                response = retry_request(
                    lambda: self.session.get(ROLLCALLS_URL, headers=headers, timeout=30),
                    max_attempts=3,
                    delay=2,
                    label="dashboard_poll",
                )
                response.raise_for_status()
                data = response.json()
                self.query_count += 1
                self.outbox.put(("poll", self.query_count, time.time(), len(data.get("rollcalls", []))))

                if data != self.last_payload:
                    self.last_payload = data
                    for rollcall in data.get("rollcalls", []):
                        rollcall_id = str(rollcall.get("rollcall_id", ""))
                        if not rollcall_id or rollcall_id in self.seen_rollcall_ids:
                            continue
                        self.seen_rollcall_ids.add(rollcall_id)
                        self.outbox.put(("rollcall", build_rollcall_event(rollcall)))
            except Exception as exc:
                self.outbox.put(("error", f"轮询失败：{exc}"))

            self.stop_event.wait(self.interval)
        self.outbox.put(("monitor_status", "已停止"))


def build_rollcall_event(rollcall: dict) -> RollcallEvent:
    if rollcall.get("is_radar"):
        rollcall_type = "雷达签到"
    elif rollcall.get("is_number"):
        rollcall_type = "数字签到"
    else:
        rollcall_type = "二维码签到"

    teacher_parts = [
        str(rollcall.get("department_name") or "").strip(),
        str(rollcall.get("created_by_name") or "").strip(),
    ]
    teacher = " ".join(part for part in teacher_parts if part) or "未知"

    return RollcallEvent(
        rollcall_id=str(rollcall.get("rollcall_id", "")),
        course_title=str(rollcall.get("course_title") or rollcall.get("course_name") or "未知课程"),
        teacher=teacher,
        rollcall_type=rollcall_type,
        status=str(rollcall.get("status") or "unknown"),
        raw=rollcall,
    )


def current_academic_year_label() -> str:
    now = time.localtime()
    start_year = now.tm_year if now.tm_mon >= 9 else now.tm_year - 1
    return f"{start_year}-{start_year + 1}"


def first_value(data: dict, keys: tuple[str, ...], default=""):
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return value
    return default


def unwrap_list(payload, keys: tuple[str, ...]):
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            nested = unwrap_list(value, keys)
            if nested:
                return nested
    for value in payload.values():
        if isinstance(value, dict):
            nested = unwrap_list(value, keys)
            if nested:
                return nested
    return []


def fetch_first_json(session, endpoints: tuple[str, ...], label: str):
    errors = []
    for endpoint in endpoints:
        url = endpoint if endpoint.startswith("http") else f"{base_url}{endpoint}"
        try:
            response = session.get(url, headers=headers, timeout=20)
            if response.status_code != 200:
                errors.append(f"{endpoint} -> HTTP {response.status_code}")
                continue
            return response.json(), endpoint
        except Exception as exc:
            errors.append(f"{endpoint} -> {exc}")
    raise RuntimeError(f"{label} 接口探测失败：" + "；".join(errors[:6]))


def normalize_course(course: dict) -> dict | None:
    if not isinstance(course, dict):
        return None
    course_id = first_value(course, ("id", "course_id", "courseId", "cid", "uuid"))
    title = first_value(course, ("name", "title", "course_title", "course_name", "display_name"))
    if not course_id or not title:
        return None
    academic_year = course.get("academic_year")
    term = ""
    if isinstance(academic_year, dict):
        term = first_value(academic_year, ("name", "code", "id"), "")
    if not term:
        term = first_value(course, ("term_name", "term", "semester", "semester_name", "academic_year", "year"), "")
    return {
        "id": str(course_id),
        "title": str(title),
        "term": str(term),
        "raw": course,
    }


def course_matches_semester(course: dict, semester: str) -> bool:
    if not semester or semester == "全部":
        return True

    raw_semester = course.get("raw", {}).get("semester")
    semester_code = ""
    if isinstance(raw_semester, dict):
        semester_code = str(raw_semester.get("code") or "")
    else:
        semester_code = str(raw_semester or course.get("term") or "")

    if semester == "第一学期":
        return semester_code.endswith("-1")
    if semester == "第二学期":
        return semester_code.endswith("-2")
    return True


def normalize_rollcall_type(rollcall: dict) -> str:
    if rollcall.get("is_radar"):
        return "雷达签到"
    if rollcall.get("is_number"):
        return "数字签到"
    if rollcall.get("is_qrcode") or rollcall.get("is_qr"):
        return "二维码签到"
    value = str(first_value(rollcall, ("rollcall_type", "type", "kind"), "")).lower()
    if "radar" in value:
        return "雷达签到"
    if "number" in value:
        return "数字签到"
    if "qr" in value:
        return "二维码签到"
    return value or "签到"


def normalize_time(value) -> str:
    if not value:
        return "-"
    text = str(value)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.strftime("%Y-%m-%d %H:%M:%S")
        return parsed.astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return text.replace("T", " ").replace("Z", "").split("+")[0]


def infer_signed_status(rollcall: dict, student_detail: dict | None, username: str) -> tuple[str, str]:
    platform_status = str(first_value(rollcall, ("status", "rollcall_status", "state"), "unknown"))
    status_text = " ".join(
        str(first_value(rollcall, keys, "")).lower()
        for keys in (
            ("status",),
            ("rollcall_status",),
            ("student_status",),
            ("state",),
        )
    )
    signed_words = ("signed", "present", "attended", "on_call_fine", "fine", "done")
    absent_words = ("absent", "missed", "not_signed", "unanswered")

    if student_detail:
        own_record = find_student_rollcall(student_detail, username)
        if own_record:
            own_status = str(first_value(own_record, ("status", "rollcall_status", "state"), "")).lower()
            if own_record.get("updated_at") or own_record.get("answered_at") or own_record.get("submitted_at"):
                return "已签到", platform_status
            if any(word in own_status for word in signed_words):
                return "已签到", platform_status
            if any(word in own_status for word in absent_words):
                return "未签到", platform_status

    if any(word in status_text for word in signed_words):
        return "已签到", platform_status
    if any(word in status_text for word in absent_words):
        return "未签到", platform_status
    return "未知", platform_status


def get_profile_user_id(session) -> str:
    response = session.get(f"{base_url}/api/profile", headers=headers, timeout=15)
    response.raise_for_status()
    profile = response.json()
    user_id = first_value(profile, ("id", "user_id", "userId", "student_id", "studentId"), "")
    if not user_id:
        raise RuntimeError("无法从 /api/profile 获取平台内部用户 ID")
    return str(user_id)


def find_student_rollcall(payload, username: str) -> dict | None:
    students = unwrap_list(payload, ("student_rollcalls", "students", "data", "items", "list"))
    for student in students:
        if not isinstance(student, dict):
            continue
        user_no = str(first_value(student, ("user_no", "username", "student_no", "number", "account"), ""))
        if username and user_no == username:
            return student
        if student.get("is_current_user") or student.get("is_self"):
            return student
    return None


def fetch_student_rollcall_detail(session, rollcall_id: str):
    if not rollcall_id:
        return None
    try:
        response = session.get(f"{base_url}/api/rollcall/{rollcall_id}/student_rollcalls", headers=headers, timeout=12)
        if response.status_code == 200:
            return response.json()
    except Exception:
        pass
    return None


def fetch_number_code(session, rollcall_id: str) -> str:
    detail = fetch_student_rollcall_detail(session, rollcall_id)
    if not detail:
        return ""
    return find_number_code(detail) or ""


def fetch_course_rollcall_records(
    session,
    username: str,
    academic_year: str,
    semester: str = "全部",
) -> tuple[list[CourseRollcallRecord], str]:
    student_id = get_profile_user_id(session)
    courses_payload, course_endpoint = fetch_first_json(session, COURSE_ENDPOINTS, "课程列表")
    raw_courses = unwrap_list(courses_payload, ("courses", "data", "items", "list", "results"))
    courses = [course for course in (normalize_course(item) for item in raw_courses) if course]

    if academic_year:
        filtered = [
            course for course in courses
            if academic_year in course["term"] or academic_year in str(course["raw"])
        ]
        if filtered:
            courses = filtered

    courses = [course for course in courses if course_matches_semester(course, semester)]

    records: list[CourseRollcallRecord] = []
    endpoint_hits: list[str] = []
    for course in courses:
        endpoints = tuple(
            template.format(course_id=course["id"], student_id=student_id)
            for template in ROLLCALL_ENDPOINT_TEMPLATES
        )
        try:
            rollcalls_payload, rollcall_endpoint = fetch_first_json(session, endpoints, f"{course['title']} 签到列表")
            if rollcall_endpoint not in endpoint_hits:
                endpoint_hits.append(rollcall_endpoint)
        except Exception as exc:
            records.append(CourseRollcallRecord(
                course_id=course["id"],
                course_title=course["title"],
                rollcall_id="-",
                rollcall_time="-",
                rollcall_type="-",
                signed_status="未知",
                platform_status="-",
                detail=str(exc),
            ))
            continue

        rollcalls = unwrap_list(rollcalls_payload, ("rollcalls", "activities", "data", "items", "list", "results"))
        if not rollcalls:
            records.append(CourseRollcallRecord(
                course_id=course["id"],
                course_title=course["title"],
                rollcall_id="-",
                rollcall_time="-",
                rollcall_type="-",
                signed_status="无签到记录",
                platform_status="-",
                detail="该课程接口未返回签到记录",
            ))
            continue

        for rollcall in rollcalls:
            if not isinstance(rollcall, dict):
                continue
            rollcall_id = str(first_value(rollcall, ("rollcall_id", "id", "activity_id"), ""))
            signed_status, platform_status = infer_signed_status(rollcall, None, username)
            records.append(CourseRollcallRecord(
                course_id=course["id"],
                course_title=course["title"],
                rollcall_id=rollcall_id or "-",
                rollcall_time=normalize_time(first_value(rollcall, ("rollcall_time", "created_at", "start_time", "time"), "")),
                rollcall_type=normalize_rollcall_type(rollcall),
                signed_status=signed_status,
                platform_status=platform_status,
                detail="",
            ))

    source = f"课程接口：{course_endpoint}；筛选：{academic_year or '全部学年'} / {semester or '全部'}"
    if endpoint_hits:
        source += f"；签到接口示例：{endpoint_hits[0]}"
    return records, source


class DashboardApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(f"XMU Rollcall Dashboard v{__version__}")
        self.root.geometry("1180x720")
        self.root.minsize(980, 640)

        self.queue: queue.Queue = queue.Queue()
        self.session = None
        self.account = None
        self.monitor_worker: MonitorWorker | None = None
        self.monitor_stop_event: threading.Event | None = None
        self.events_by_item: dict[str, RollcallEvent] = {}
        self.course_records: list[CourseRollcallRecord] = []
        self.grade_records: list[GradeRecord] = []
        self.courseware_courses: list[CourseSummary] = []
        self.courseware_items: list[CoursewareItem] = []
        self.courseware_course_by_display: dict[str, CourseSummary] = {}
        self.courseware_items_by_tree_id: dict[str, CoursewareItem] = {}
        self.course_refresh_in_progress = False
        self.grade_refresh_in_progress = False
        self.courseware_courses_refresh_in_progress = False
        self.courseware_refresh_in_progress = False
        self.courseware_download_in_progress = False
        self.started_at: float | None = None
        self.auto_answer_var = tk.BooleanVar(value=False)
        self.poll_interval_var = tk.IntVar(value=DEFAULT_POLL_INTERVAL_SECONDS)
        self.academic_year_var = tk.StringVar(value=current_academic_year_label())
        self.semester_var = tk.StringVar(value="全部")
        self.only_unsigned_var = tk.BooleanVar(value=False)
        self.course_summary_var = tk.StringVar(value="登录后点击刷新，查看课程签到数据。")
        self.grade_term_var = tk.StringVar(value="全部")
        self.grade_summary_var = tk.StringVar(value="登录后点击刷新，查询本人教务成绩。")
        self.courseware_course_var = tk.StringVar()
        self.courseware_summary_var = tk.StringVar(value="登录后自动读取课程和课件。")
        self.courseware_download_dir_var = tk.StringVar(
            value=str(Path.home() / "Downloads" / "XMU-Courseware")
        )

        self.status_var = tk.StringVar(value="未登录")
        self.account_var = tk.StringVar(value="未登录")
        self.monitor_status_var = tk.StringVar(value="未启动")
        self.query_count_var = tk.StringVar(value="0")
        self.runtime_var = tk.StringVar(value="0s")
        self.last_check_var = tk.StringVar(value="-")
        self.rollcall_count_var = tk.StringVar(value="0")
        self.last_result_var = tk.StringVar(value="-")

        self.username_var = tk.StringVar()
        self.password_var = tk.StringVar()

        self._build_ui()
        self._load_current_account_hint()
        self.root.after(400, self.auto_restore_current_session)
        self.root.after(200, self._drain_queue)
        self.root.after(1000, self._tick_runtime)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self):
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)

        header = ttk.Frame(self.root, padding=(14, 12))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(1, weight=1)

        ttk.Label(header, text="XMU 签到看板", font=("Microsoft YaHei UI", 18, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(header, textvariable=self.status_var).grid(row=0, column=1, sticky="e")

        notebook = ttk.Notebook(self.root)
        notebook.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))

        dashboard_tab = ttk.Frame(notebook, padding=12)
        events_tab = ttk.Frame(notebook, padding=12)
        course_tab = ttk.Frame(notebook, padding=12)
        grades_tab = ttk.Frame(notebook, padding=12)
        courseware_tab = ttk.Frame(notebook, padding=12)
        settings_tab = ttk.Frame(notebook, padding=12)
        logs_tab = ttk.Frame(notebook, padding=12)

        notebook.add(dashboard_tab, text="总览")
        notebook.add(events_tab, text="签到事件")
        notebook.add(course_tab, text="课程签到总览")
        notebook.add(grades_tab, text="成绩查询")
        notebook.add(courseware_tab, text="课程课件")
        notebook.add(settings_tab, text="策略")
        notebook.add(logs_tab, text="日志")

        self._build_dashboard_tab(dashboard_tab)
        self._build_events_tab(events_tab)
        self._build_course_tab(course_tab)
        self._build_grades_tab(grades_tab)
        self._build_courseware_tab(courseware_tab)
        self._build_settings_tab(settings_tab)
        self._build_logs_tab(logs_tab)

    def _build_dashboard_tab(self, parent):
        parent.columnconfigure(0, weight=1)
        parent.columnconfigure(1, weight=1)
        parent.rowconfigure(2, weight=1)

        login_box = ttk.LabelFrame(parent, text="账号登录", padding=12)
        login_box.grid(row=0, column=0, sticky="nsew", padx=(0, 8), pady=(0, 12))
        login_box.columnconfigure(1, weight=1)

        ttk.Label(login_box, text="学号").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Entry(login_box, textvariable=self.username_var).grid(row=0, column=1, sticky="ew", pady=4)
        ttk.Label(login_box, text="密码").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(login_box, textvariable=self.password_var, show="*").grid(row=1, column=1, sticky="ew", pady=4)

        button_row = ttk.Frame(login_box)
        button_row.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        ttk.Button(button_row, text="登录", command=self.login).pack(side="left")
        ttk.Button(button_row, text="退出登录", command=self.logout).pack(side="left", padx=8)

        status_box = ttk.LabelFrame(parent, text="运行状态", padding=12)
        status_box.grid(row=0, column=1, sticky="nsew", padx=(8, 0), pady=(0, 12))
        for row, (label, var) in enumerate(
            [
                ("当前账号", self.account_var),
                ("监控状态", self.monitor_status_var),
                ("轮询次数", self.query_count_var),
                ("运行时长", self.runtime_var),
                ("最近检查", self.last_check_var),
                ("当前签到数", self.rollcall_count_var),
                ("最近结果", self.last_result_var),
            ]
        ):
            ttk.Label(status_box, text=label).grid(row=row, column=0, sticky="w", pady=3)
            ttk.Label(status_box, textvariable=var).grid(row=row, column=1, sticky="e", pady=3)
        status_box.columnconfigure(1, weight=1)

        controls = ttk.Frame(parent)
        controls.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        ttk.Button(controls, text="启动监控", command=self.start_monitor).pack(side="left")
        ttk.Button(controls, text="停止监控", command=self.stop_monitor).pack(side="left", padx=8)
        ttk.Checkbutton(controls, text="检测后自动签到（谨慎）", variable=self.auto_answer_var).pack(side="left", padx=16)

        recent_box = ttk.LabelFrame(parent, text="最近签到事件", padding=8)
        recent_box.grid(row=2, column=0, columnspan=2, sticky="nsew")
        recent_box.rowconfigure(0, weight=1)
        recent_box.columnconfigure(0, weight=1)
        self.recent_tree = self._create_events_tree(recent_box)
        self.recent_tree.grid(row=0, column=0, sticky="nsew")

    def _build_events_tab(self, parent):
        parent.rowconfigure(0, weight=1)
        parent.columnconfigure(0, weight=1)
        self.events_tree = self._create_events_tree(parent)
        self.events_tree.grid(row=0, column=0, sticky="nsew")

        action_row = ttk.Frame(parent)
        action_row.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        ttk.Button(action_row, text="立即处理选中签到", command=self.answer_selected_rollcall).pack(side="left")
        ttk.Button(action_row, text="标记为跳过", command=self.skip_selected_rollcall).pack(side="left", padx=8)

    def _build_course_tab(self, parent):
        parent.rowconfigure(1, weight=1)
        parent.columnconfigure(0, weight=1)

        toolbar = ttk.Frame(parent)
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        ttk.Label(toolbar, text="学年").pack(side="left")
        ttk.Entry(toolbar, textvariable=self.academic_year_var, width=14).pack(side="left", padx=8)
        ttk.Label(toolbar, text="学期").pack(side="left")
        ttk.Combobox(
            toolbar,
            textvariable=self.semester_var,
            values=("全部", "第一学期", "第二学期"),
            state="readonly",
            width=10,
        ).pack(side="left", padx=8)
        ttk.Button(toolbar, text="刷新课程签到数据", command=self.refresh_course_rollcalls).pack(side="left")
        ttk.Checkbutton(
            toolbar,
            text="只显示未签",
            variable=self.only_unsigned_var,
            command=self._refresh_course_tree,
        ).pack(side="right")
        ttk.Label(toolbar, textvariable=self.course_summary_var).pack(side="left", padx=14)

        table_frame = ttk.Frame(parent)
        table_frame.grid(row=1, column=0, sticky="nsew")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)

        columns = ("course", "time", "type", "signed", "platform_status", "rollcall_id", "detail")
        self.course_tree = ttk.Treeview(table_frame, columns=columns, show="headings", height=14)
        self.course_tree.heading("course", text="课程")
        self.course_tree.heading("time", text="签到时间")
        self.course_tree.heading("type", text="类型")
        self.course_tree.heading("signed", text="是否签到")
        self.course_tree.heading("platform_status", text="平台状态")
        self.course_tree.heading("rollcall_id", text="签到 ID")
        self.course_tree.heading("detail", text="详情")
        self.course_tree.column("course", width=240)
        self.course_tree.column("time", width=150, anchor="center")
        self.course_tree.column("type", width=90, anchor="center")
        self.course_tree.column("signed", width=90, anchor="center")
        self.course_tree.column("platform_status", width=100, anchor="center")
        self.course_tree.column("rollcall_id", width=90, anchor="center")
        self.course_tree.column("detail", width=240)
        self.course_tree.grid(row=0, column=0, sticky="nsew")

        scrollbar = ttk.Scrollbar(table_frame, command=self.course_tree.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.course_tree.configure(yscrollcommand=scrollbar.set)

    def _build_grades_tab(self, parent):
        parent.rowconfigure(1, weight=1)
        parent.columnconfigure(0, weight=1)

        toolbar = ttk.Frame(parent)
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        ttk.Button(toolbar, text="刷新成绩", command=self.refresh_grades).pack(side="left")
        ttk.Label(toolbar, text="学期").pack(side="left", padx=(16, 0))
        self.grade_term_combo = ttk.Combobox(
            toolbar,
            textvariable=self.grade_term_var,
            values=("全部",),
            state="readonly",
            width=24,
        )
        self.grade_term_combo.pack(side="left", padx=8)
        self.grade_term_combo.bind("<<ComboboxSelected>>", lambda _event: self._refresh_grade_tree())
        ttk.Label(toolbar, textvariable=self.grade_summary_var).pack(side="left", padx=14)

        table_frame = ttk.Frame(parent)
        table_frame.grid(row=1, column=0, sticky="nsew")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)

        columns = (
            "term", "course", "code", "credit", "score", "point",
            "nature", "assessment", "composition",
        )
        self.grade_tree = ttk.Treeview(table_frame, columns=columns, show="headings", height=16)
        headings = {
            "term": "学期",
            "course": "课程名称",
            "code": "课程号",
            "credit": "学分",
            "score": "成绩",
            "point": "绩点",
            "nature": "课程性质",
            "assessment": "考核方式",
            "composition": "成绩构成",
        }
        for column, heading in headings.items():
            self.grade_tree.heading(column, text=heading)
        self.grade_tree.column("term", width=180)
        self.grade_tree.column("course", width=220)
        self.grade_tree.column("code", width=130, anchor="center")
        self.grade_tree.column("credit", width=60, anchor="center")
        self.grade_tree.column("score", width=70, anchor="center")
        self.grade_tree.column("point", width=60, anchor="center")
        self.grade_tree.column("nature", width=90, anchor="center")
        self.grade_tree.column("assessment", width=80, anchor="center")
        self.grade_tree.column("composition", width=250)
        self.grade_tree.grid(row=0, column=0, sticky="nsew")

        vertical = ttk.Scrollbar(table_frame, orient="vertical", command=self.grade_tree.yview)
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal = ttk.Scrollbar(table_frame, orient="horizontal", command=self.grade_tree.xview)
        horizontal.grid(row=1, column=0, sticky="ew")
        self.grade_tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)

    def _build_courseware_tab(self, parent):
        parent.rowconfigure(1, weight=1)
        parent.columnconfigure(0, weight=1)

        toolbar = ttk.Frame(parent)
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        ttk.Label(toolbar, text="课程").pack(side="left")
        self.courseware_course_combo = ttk.Combobox(
            toolbar,
            textvariable=self.courseware_course_var,
            values=(),
            state="readonly",
            width=46,
        )
        self.courseware_course_combo.pack(side="left", padx=8)
        self.courseware_course_combo.bind(
            "<<ComboboxSelected>>",
            lambda _event: self.refresh_selected_courseware(),
        )
        ttk.Button(toolbar, text="刷新课程", command=self.refresh_courseware_courses).pack(side="left")
        ttk.Button(toolbar, text="刷新课件", command=self.refresh_selected_courseware).pack(side="left", padx=8)
        ttk.Label(toolbar, textvariable=self.courseware_summary_var).pack(side="left", padx=10)

        table_frame = ttk.Frame(parent)
        table_frame.grid(row=1, column=0, sticky="nsew")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)
        columns = (
            "module", "syllabus", "activity", "filename", "type",
            "size", "published", "download", "status",
        )
        self.courseware_tree = ttk.Treeview(table_frame, columns=columns, show="headings", height=16)
        headings = {
            "module": "章节",
            "syllabus": "小节",
            "activity": "课件活动",
            "filename": "文件名",
            "type": "类型",
            "size": "大小",
            "published": "发布时间",
            "download": "下载权限",
            "status": "文件状态",
        }
        for column, heading in headings.items():
            self.courseware_tree.heading(column, text=heading)
        self.courseware_tree.column("module", width=100)
        self.courseware_tree.column("syllabus", width=120)
        self.courseware_tree.column("activity", width=230)
        self.courseware_tree.column("filename", width=260)
        self.courseware_tree.column("type", width=80, anchor="center")
        self.courseware_tree.column("size", width=80, anchor="e")
        self.courseware_tree.column("published", width=145, anchor="center")
        self.courseware_tree.column("download", width=120, anchor="center")
        self.courseware_tree.column("status", width=90, anchor="center")
        self.courseware_tree.grid(row=0, column=0, sticky="nsew")
        self.courseware_tree.bind("<Double-1>", lambda _event: self.activate_selected_courseware())

        vertical = ttk.Scrollbar(table_frame, orient="vertical", command=self.courseware_tree.yview)
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal = ttk.Scrollbar(table_frame, orient="horizontal", command=self.courseware_tree.xview)
        horizontal.grid(row=1, column=0, sticky="ew")
        self.courseware_tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.courseware_tree.tag_configure("downloadable", foreground="#176b32")
        self.courseware_tree.tag_configure("blocked", foreground="#777777")

        actions = ttk.Frame(parent)
        actions.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        ttk.Button(actions, text="下载选中", command=self.download_selected_courseware).pack(side="left")
        ttk.Button(actions, text="下载全部可下载课件", command=self.download_all_courseware).pack(side="left", padx=8)
        ttk.Button(actions, text="打开平台页面", command=self.open_selected_courseware).pack(side="left")
        ttk.Button(actions, text="选择目录", command=self.choose_courseware_download_dir).pack(side="left", padx=8)
        ttk.Label(actions, textvariable=self.courseware_download_dir_var).pack(side="left", padx=10)

    def _build_settings_tab(self, parent):
        parent.columnconfigure(0, weight=1)

        policy = ttk.LabelFrame(parent, text="监控策略", padding=12)
        policy.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        ttk.Label(policy, text="轮询间隔（秒）").grid(row=0, column=0, sticky="w")
        ttk.Spinbox(policy, from_=1, to=30, textvariable=self.poll_interval_var, width=8).grid(row=0, column=1, sticky="w", padx=8)

        ttk.Label(
            policy,
            text="说明：看板默认只提醒并等待手动确认。开启自动签到后，会在检测到数字/雷达签到时直接提交。",
            wraplength=760,
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(10, 0))

    def _build_logs_tab(self, parent):
        parent.rowconfigure(0, weight=1)
        parent.columnconfigure(0, weight=1)
        self.log_text = tk.Text(parent, height=20, wrap="word")
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(parent, command=self.log_text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scrollbar.set)

    def _create_events_tree(self, parent):
        columns = ("time", "course", "teacher", "type", "status", "code", "result", "detail")
        tree = ttk.Treeview(parent, columns=columns, show="headings", height=10)
        tree.heading("time", text="时间")
        tree.heading("course", text="课程")
        tree.heading("teacher", text="发起人")
        tree.heading("type", text="类型")
        tree.heading("status", text="平台状态")
        tree.heading("code", text="签到码")
        tree.heading("result", text="处理状态")
        tree.heading("detail", text="详情")
        tree.column("time", width=90, anchor="center")
        tree.column("course", width=190)
        tree.column("teacher", width=150)
        tree.column("type", width=90, anchor="center")
        tree.column("status", width=90, anchor="center")
        tree.column("code", width=80, anchor="center")
        tree.column("result", width=100, anchor="center")
        tree.column("detail", width=210)
        return tree

    def _load_current_account_hint(self):
        try:
            config = load_config()
            account = get_current_account(config)
            if account:
                self.username_var.set(account.get("username", ""))
                if self.session:
                    self.account_var.set(account.get("name") or account.get("username") or "已登录")
        except Exception as exc:
            self.log(f"读取配置失败：{exc}")

    def login(self):
        username = self.username_var.get().strip()
        password = self.password_var.get().strip()
        if not username or not password:
            messagebox.showwarning("缺少信息", "请输入学号和密码。")
            return

        self.status_var.set("正在登录...")
        self.log("开始登录。")
        threading.Thread(target=self._login_worker, args=(username, password), daemon=True).start()

    def _login_worker(self, username, password):
        try:
            session = xmulogin(type=3, username=username, password=password)
            if not session:
                self.queue.put(("login_failed", "登录失败，请检查学号或密码。"))
                return

            try:
                profile = session.get(f"{base_url}/api/profile", headers=headers, timeout=15).json()
                name = profile.get("name") or username
            except Exception:
                name = username

            config = load_config()
            account = None
            for existing in get_all_accounts(config):
                if existing.get("username") == username:
                    account = existing
                    break
            if account is None:
                account_id = add_account(config, username, password, name)
                account = next(acc for acc in get_all_accounts(config) if acc.get("id") == account_id)
            else:
                account["password"] = password
                account["name"] = name

            set_current_account(config, account["id"])
            set_rollcall_settings(account, get_rollcall_settings(account))
            save_config(config)
            save_session(session, get_cookies_path(account["id"]))

            self.queue.put(("login_success", session, account))
        except Exception as exc:
            self.queue.put(("login_failed", str(exc)))

    def auto_restore_current_session(self):
        if self.session:
            return
        try:
            config = load_config()
            account = get_current_account(config)
            if not account:
                return
            cookies_path = get_cookies_path(account.get("id", 1))
            if not os.path.exists(cookies_path):
                return
            self.status_var.set("正在自动恢复会话...")
            self.log("检测到上次登录会话，正在自动恢复。")
            threading.Thread(target=self._restore_worker, args=(True,), daemon=True).start()
        except Exception as exc:
            self.log(f"自动恢复检查失败：{exc}")

    def _restore_worker(self, silent=False):
        try:
            config = load_config()
            account = get_current_account(config)
            if not account:
                self.queue.put(("restore_failed", "没有可恢复的当前账号，请先登录。", silent))
                return
            session = requests.Session()
            if not load_session(session, get_cookies_path(account.get("id", 1))):
                self.queue.put(("restore_failed", "Cookie 读取失败，请重新登录。", silent))
                return
            if not verify_session(session):
                self.queue.put(("restore_failed", "登录态已失效，请重新登录。", silent))
                return
            self.queue.put(("login_success", session, account))
        except Exception as exc:
            self.queue.put(("restore_failed", str(exc), silent))

    def logout(self):
        if not self.account and not self.session:
            self.status_var.set("未登录")
            self.account_var.set("未登录")
            return
        if not messagebox.askyesno("退出登录", "确定要退出登录并清除当前账号的本地 Cookie 吗？"):
            return

        self.stop_monitor()
        account = self.account
        self.session = None
        self.account = None
        self.password_var.set("")
        self.status_var.set("未登录")
        self.account_var.set("未登录")
        self.monitor_status_var.set("未启动")
        self.last_result_var.set("-")
        self.course_refresh_in_progress = False
        self.grade_refresh_in_progress = False
        self.courseware_courses_refresh_in_progress = False
        self.courseware_refresh_in_progress = False
        self.courseware_download_in_progress = False
        self.grade_records = []
        self.grade_term_var.set("全部")
        self.grade_term_combo.configure(values=("全部",))
        self._refresh_grade_tree()
        self.grade_summary_var.set("登录后点击刷新，查询本人教务成绩。")
        self.courseware_courses = []
        self.courseware_items = []
        self.courseware_course_by_display = {}
        self.courseware_course_combo.configure(values=())
        self.courseware_course_var.set("")
        self._refresh_courseware_tree()
        self.courseware_summary_var.set("登录后自动读取课程和课件。")

        if account:
            try:
                cookies_path = get_cookies_path(account.get("id", 1))
                if os.path.exists(cookies_path):
                    os.remove(cookies_path)
                self.log("已退出登录并清除本地 Cookie。")
            except Exception as exc:
                self.log(f"清除 Cookie 失败：{exc}")

    def start_monitor(self):
        if not self.session or not self.account:
            messagebox.showwarning("尚未登录", "请先登录。")
            return
        if self.monitor_worker and self.monitor_worker.is_alive():
            return

        self.monitor_stop_event = threading.Event()
        self.monitor_worker = MonitorWorker(
            self.session,
            self.account,
            self.queue,
            self.monitor_stop_event,
            self.poll_interval_var.get(),
        )
        self.started_at = time.time()
        self.monitor_worker.start()
        self.log("监控已启动。")

    def stop_monitor(self):
        if self.monitor_stop_event:
            self.monitor_stop_event.set()
        self.log("正在停止监控。")

    def answer_selected_rollcall(self):
        item_id = self._selected_item_id()
        if not item_id:
            messagebox.showinfo("未选择", "请先选择一条签到事件。")
            return
        event = self.events_by_item[item_id]
        self._answer_event(item_id, event)

    def skip_selected_rollcall(self):
        item_id = self._selected_item_id()
        if not item_id:
            return
        self._update_event_result(item_id, "已跳过", "用户手动跳过")

    def refresh_course_rollcalls(self, silent=False):
        if not self.session or not self.account:
            if not silent:
                messagebox.showwarning("尚未登录", "请先登录。")
            return
        if self.course_refresh_in_progress:
            return
        self.course_refresh_in_progress = True
        self.course_summary_var.set("正在刷新课程签到数据...")
        self.log("开始刷新课程签到总览。")
        threading.Thread(target=self._course_rollcalls_worker, args=(silent,), daemon=True).start()

    def _course_rollcalls_worker(self, silent=False):
        try:
            username = str(self.account.get("username", "")) if self.account else ""
            records, source = fetch_course_rollcall_records(
                self.session,
                username=username,
                academic_year=self.academic_year_var.get().strip(),
                semester=self.semester_var.get().strip(),
            )
            self.queue.put(("course_rollcalls", records, source))
        except Exception as exc:
            self.queue.put(("course_rollcalls_error", str(exc), silent))

    def refresh_courseware_courses(self, silent=False):
        if not self.session:
            if not silent:
                messagebox.showwarning("尚未登录", "请先在总览页登录。")
            return
        if self.courseware_courses_refresh_in_progress:
            return
        self.courseware_courses_refresh_in_progress = True
        self.courseware_summary_var.set("正在读取课程列表...")
        self.log("开始读取课件课程列表。")
        threading.Thread(target=self._courseware_courses_worker, args=(silent,), daemon=True).start()

    def _courseware_courses_worker(self, silent=False):
        try:
            courses, source = fetch_courses(self.session)
            self.queue.put(("courseware_courses", courses, source))
        except Exception as exc:
            self.queue.put(("courseware_courses_error", str(exc), silent))

    def _selected_courseware_course(self) -> CourseSummary | None:
        return self.courseware_course_by_display.get(self.courseware_course_var.get())

    def refresh_selected_courseware(self, silent=False):
        if not self.session:
            if not silent:
                messagebox.showwarning("尚未登录", "请先在总览页登录。")
            return
        course = self._selected_courseware_course()
        if not course:
            if not silent:
                messagebox.showinfo("未选择课程", "请先选择一门课程。")
            return
        if self.courseware_refresh_in_progress:
            return
        self.courseware_refresh_in_progress = True
        self.courseware_summary_var.set(f"正在读取《{course.title}》课件...")
        self.log(f"开始读取课程课件：{course.title}。")
        threading.Thread(
            target=self._courseware_worker,
            args=(course, silent),
            daemon=True,
        ).start()

    def _courseware_worker(self, course: CourseSummary, silent=False):
        try:
            items = fetch_courseware(self.session, course.course_id)
            self.queue.put(("courseware", course, items))
        except Exception as exc:
            self.queue.put(("courseware_error", str(exc), silent))

    def choose_courseware_download_dir(self):
        selected = filedialog.askdirectory(
            title="选择课件下载目录",
            initialdir=self.courseware_download_dir_var.get() or str(Path.home()),
        )
        if selected:
            self.courseware_download_dir_var.set(selected)

    def download_selected_courseware(self):
        selection = self.courseware_tree.selection()
        if not selection:
            messagebox.showinfo("未选择", "请先选择一个课件附件。")
            return
        item = self.courseware_items_by_tree_id.get(selection[0])
        if not item:
            return
        if item.download_status != "可下载":
            messagebox.showwarning("无法下载", item.download_status)
            return
        self._start_courseware_download([item])

    def activate_selected_courseware(self):
        selection = self.courseware_tree.selection()
        if not selection:
            return
        item = self.courseware_items_by_tree_id.get(selection[0])
        if item and item.download_status == "可下载":
            self._start_courseware_download([item])
        elif item:
            self.open_selected_courseware()

    def open_selected_courseware(self):
        selection = self.courseware_tree.selection()
        if not selection:
            messagebox.showinfo("未选择", "请先选择一个课件活动。")
            return
        item = self.courseware_items_by_tree_id.get(selection[0])
        if not item:
            return
        url = f"{base_url}/course/{item.course_id}/learning-activity#/{item.activity_id}"
        if not webbrowser.open(url):
            messagebox.showerror("打开失败", "无法调用系统浏览器打开平台页面。")

    def download_all_courseware(self):
        items = [item for item in self.courseware_items if item.download_status == "可下载"]
        if not items:
            messagebox.showinfo("没有可下载课件", "当前课程没有平台授权下载的附件。")
            return
        total_size = sum(item.size for item in items)
        if not messagebox.askyesno(
            "确认批量下载",
            f"将下载 {len(items)} 个文件，共 {format_size(total_size)}。是否继续？",
        ):
            return
        self._start_courseware_download(items)

    def _start_courseware_download(self, items: list[CoursewareItem]):
        if self.courseware_download_in_progress:
            messagebox.showinfo("正在下载", "请等待当前下载任务完成。")
            return
        course = self._selected_courseware_course()
        if not course:
            return
        self.courseware_download_in_progress = True
        destination = Path(self.courseware_download_dir_var.get()) / sanitize_filename(course.title)
        self.courseware_summary_var.set(f"准备下载 {len(items)} 个文件...")
        threading.Thread(
            target=self._courseware_download_worker,
            args=(items, destination),
            daemon=True,
        ).start()

    def _courseware_download_worker(self, items: list[CoursewareItem], destination: Path):
        downloaded = []
        errors = []
        for index, item in enumerate(items, start=1):
            self.queue.put(("courseware_download_progress", index, len(items), item.filename))
            try:
                downloaded.append(download_courseware(self.session, item, destination))
            except Exception as exc:
                errors.append(f"{item.filename or item.activity_title}：{exc}")
        self.queue.put(("courseware_download_done", downloaded, errors, destination))

    def refresh_grades(self, silent=False):
        if not self.account:
            if not silent:
                messagebox.showwarning("尚未登录", "请先在总览页登录。")
            return
        if not self.account.get("password"):
            if silent:
                self.grade_summary_var.set("需要重新登录后才能自动查询成绩")
                self.log("自动查询成绩跳过：当前账号没有已保存密码。")
            else:
                messagebox.showwarning("缺少密码", "请在总览页重新输入密码并登录。")
            return
        if self.grade_refresh_in_progress:
            return
        self.grade_refresh_in_progress = True
        self.grade_summary_var.set("正在登录教务系统并查询成绩...")
        self.log("开始查询本人教务成绩。")
        threading.Thread(target=self._grades_worker, args=(silent,), daemon=True).start()

    def _grades_worker(self, silent=False):
        try:
            records = fetch_grade_records(
                str(self.account.get("username", "")),
                str(self.account.get("password", "")),
            )
            self.queue.put(("grades", records))
        except Exception as exc:
            self.queue.put(("grades_error", str(exc), silent))

    def _refresh_after_login(self):
        self.log("登录完成，开始后台刷新课程签到总览、成绩和课件课程。")
        self.refresh_course_rollcalls(silent=True)
        self.refresh_grades(silent=True)
        self.refresh_courseware_courses(silent=True)

    def _selected_item_id(self):
        selection = self.events_tree.selection() or self.recent_tree.selection()
        return selection[0] if selection else ""

    def _answer_event(self, item_id: str, event: RollcallEvent):
        if not self.session:
            messagebox.showwarning("尚未登录", "请先登录。")
            return
        if event.rollcall_type == "二维码签到":
            self._update_event_result(item_id, "需手动", "二维码签到需要手动扫码")
            messagebox.showinfo("需要手动处理", "二维码签到暂不支持自动处理，请手动扫码。")
            return

        self._update_event_result(item_id, "处理中", "正在提交")
        threading.Thread(target=self._answer_worker, args=(item_id, event), daemon=True).start()

    def _answer_worker(self, item_id: str, event: RollcallEvent):
        try:
            if event.rollcall_type == "数字签到":
                ok = send_code(self.session, event.rollcall_id)
            elif event.rollcall_type == "雷达签到":
                ok = send_radar(self.session, event.rollcall_id)
            else:
                ok = False
            self.queue.put(("answer_result", item_id, ok, "提交成功" if ok else "提交失败"))
        except Exception as exc:
            self.queue.put(("answer_result", item_id, False, str(exc)))

    def _drain_queue(self):
        while True:
            try:
                event = self.queue.get_nowait()
            except queue.Empty:
                break
            self._handle_event(event)
        self.root.after(200, self._drain_queue)

    def _handle_event(self, event):
        kind = event[0]
        if kind == "login_success":
            _, session, account = event
            self.session = session
            self.account = account
            self.status_var.set("已登录")
            self.account_var.set(account.get("name") or account.get("username") or "已登录")
            self.log(f"登录成功：{self.account_var.get()}")
            self._refresh_after_login()
        elif kind == "login_failed":
            self.status_var.set("未登录")
            self.account_var.set("未登录")
            self.log(f"登录失败：{event[1]}")
            messagebox.showerror("登录失败", event[1])
        elif kind == "restore_failed":
            _, message, silent = event
            self.status_var.set("未登录")
            self.account_var.set("未登录")
            self.log(f"会话恢复失败：{message}")
            if not silent:
                messagebox.showerror("恢复失败", message)
        elif kind == "monitor_status":
            self.monitor_status_var.set(event[1])
            self.log(f"监控状态：{event[1]}")
        elif kind == "poll":
            _, query_count, checked_at, rollcall_count = event
            self.query_count_var.set(str(query_count))
            self.last_check_var.set(time.strftime("%H:%M:%S", time.localtime(checked_at)))
            self.rollcall_count_var.set(str(rollcall_count))
        elif kind == "rollcall":
            self._add_rollcall_event(event[1])
        elif kind == "answer_result":
            _, item_id, ok, detail = event
            self._update_event_result(item_id, "已签到" if ok else "失败", detail)
            self.last_result_var.set(detail)
            self.log(f"签到处理结果：{detail}")
        elif kind == "number_code":
            item_id = event[1]
            code = event[2]
            detail = event[3] if len(event) > 3 else ""
            self._update_event_code(item_id, code, detail)
        elif kind == "course_rollcalls":
            _, records, source = event
            self.course_refresh_in_progress = False
            self._set_course_records(records, source)
        elif kind == "course_rollcalls_error":
            self.course_refresh_in_progress = False
            self.course_summary_var.set("刷新失败")
            self.log(f"课程签到总览刷新失败：{event[1]}")
            silent = event[2] if len(event) > 2 else False
            if not silent:
                messagebox.showerror("刷新失败", event[1])
        elif kind == "grades":
            self.grade_refresh_in_progress = False
            self._set_grade_records(event[1])
        elif kind == "grades_error":
            self.grade_refresh_in_progress = False
            self.grade_summary_var.set("查询失败")
            self.log(f"成绩查询失败：{event[1]}")
            silent = event[2] if len(event) > 2 else False
            if not silent:
                messagebox.showerror("成绩查询失败", event[1])
        elif kind == "courseware_courses":
            self.courseware_courses_refresh_in_progress = False
            self._set_courseware_courses(event[1], event[2])
        elif kind == "courseware_courses_error":
            self.courseware_courses_refresh_in_progress = False
            self.courseware_summary_var.set("课程列表读取失败")
            self.log(f"课件课程列表读取失败：{event[1]}")
            silent = event[2] if len(event) > 2 else False
            if not silent:
                messagebox.showerror("课程列表读取失败", event[1])
        elif kind == "courseware":
            self.courseware_refresh_in_progress = False
            self._set_courseware_items(event[1], event[2])
        elif kind == "courseware_error":
            self.courseware_refresh_in_progress = False
            self.courseware_summary_var.set("课件读取失败")
            self.log(f"课程课件读取失败：{event[1]}")
            silent = event[2] if len(event) > 2 else False
            if not silent:
                messagebox.showerror("课件读取失败", event[1])
        elif kind == "courseware_download_progress":
            _, index, total, filename = event
            self.courseware_summary_var.set(f"正在下载 {index}/{total}：{filename}")
        elif kind == "courseware_download_done":
            self.courseware_download_in_progress = False
            _, downloaded, errors, destination = event
            self.courseware_summary_var.set(f"下载完成 {len(downloaded)} 个；失败 {len(errors)} 个")
            self.log(f"课件下载完成：{len(downloaded)} 个，目录：{destination}")
            if errors:
                self.log("课件下载失败：" + "；".join(errors))
                messagebox.showwarning(
                    "部分下载失败",
                    f"成功 {len(downloaded)} 个，失败 {len(errors)} 个。\n" + "\n".join(errors[:8]),
                )
            else:
                messagebox.showinfo("下载完成", f"已下载 {len(downloaded)} 个文件到：\n{destination}")
        elif kind == "error":
            self.log(event[1])

    def _add_rollcall_event(self, event: RollcallEvent):
        values = self._event_values(event)
        item_id = self.events_tree.insert("", 0, values=values)
        self.recent_tree.insert("", 0, iid=item_id, values=values)
        self.events_by_item[item_id] = event
        self.last_result_var.set(f"检测到 {event.rollcall_type}")
        self.log(f"检测到签到：{event.course_title} / {event.rollcall_type}")

        if event.rollcall_type == "数字签到":
            threading.Thread(target=self._number_code_worker, args=(item_id, event), daemon=True).start()

        if self.auto_answer_var.get():
            self._answer_event(item_id, event)

    def _number_code_worker(self, item_id: str, event: RollcallEvent):
        try:
            code = fetch_number_code(self.session, event.rollcall_id)
            self.queue.put(("number_code", item_id, code))
        except Exception as exc:
            self.queue.put(("number_code", item_id, "", str(exc)))

    def _event_values(self, event: RollcallEvent):
        return (
            time.strftime("%H:%M:%S", time.localtime(event.detected_at)),
            event.course_title,
            event.teacher,
            event.rollcall_type,
            event.status,
            event.number_code or "-",
            event.result,
            event.detail,
        )

    def _update_event_result(self, item_id: str, result: str, detail: str):
        event = self.events_by_item.get(item_id)
        if not event:
            return
        event.result = result
        event.detail = detail
        values = self._event_values(event)
        if self.events_tree.exists(item_id):
            self.events_tree.item(item_id, values=values)
        if self.recent_tree.exists(item_id):
            self.recent_tree.item(item_id, values=values)

    def _update_event_code(self, item_id: str, code: str, detail: str = ""):
        event = self.events_by_item.get(item_id)
        if not event:
            return
        if code:
            event.number_code = code
            if not event.detail:
                event.detail = "已获取签到码"
            self.log(f"数字签到码已获取：{event.course_title} / {code}")
        elif detail:
            event.detail = f"签到码获取失败：{detail}"
            self.log(event.detail)
        else:
            event.detail = "未获取到签到码"
            self.log(f"未获取到数字签到码：{event.course_title}")

        values = self._event_values(event)
        if self.events_tree.exists(item_id):
            self.events_tree.item(item_id, values=values)
        if self.recent_tree.exists(item_id):
            self.recent_tree.item(item_id, values=values)

    def _set_course_records(self, records: list[CourseRollcallRecord], source: str):
        self.course_records = records
        self._refresh_course_tree()
        self.log(f"课程签到总览刷新完成。{source}")

    def _set_courseware_courses(self, courses: list[CourseSummary], source: str):
        previous = self.courseware_course_var.get()
        self.courseware_courses = courses
        self.courseware_course_by_display = {}
        displays = []
        for course in courses:
            display = course.display_name
            if display in self.courseware_course_by_display:
                display = f"{display}  ({course.course_id})"
            self.courseware_course_by_display[display] = course
            displays.append(display)
        self.courseware_course_combo.configure(values=displays)
        if previous in self.courseware_course_by_display:
            self.courseware_course_var.set(previous)
        elif displays:
            self.courseware_course_var.set(displays[0])
        else:
            self.courseware_course_var.set("")
        self.courseware_summary_var.set(f"共 {len(courses)} 门课程")
        self.log(f"课件课程列表读取完成，共 {len(courses)} 门；接口：{source}")
        if displays:
            self.refresh_selected_courseware(silent=True)

    def _set_courseware_items(self, course: CourseSummary, items: list[CoursewareItem]):
        selected = self._selected_courseware_course()
        if selected and selected.course_id != course.course_id:
            self.refresh_selected_courseware(silent=True)
            return
        self.courseware_items = items
        self._refresh_courseware_tree()
        downloadable = sum(item.download_status == "可下载" for item in items)
        blocked = sum(item.download_status == "仅可预览（未开放下载）" for item in items)
        self.courseware_summary_var.set(
            f"课件 {len(items)} 条；可下载 {downloadable}；仅可预览 {blocked}"
        )
        self.log(
            f"课程课件读取完成：{course.title}，共 {len(items)} 条，可下载 {downloadable} 条。"
        )

    def _refresh_courseware_tree(self):
        for tree_id in self.courseware_tree.get_children():
            self.courseware_tree.delete(tree_id)
        self.courseware_items_by_tree_id = {}
        type_names = {
            "material": "资料",
            "online_video": "视频",
            "slide": "课件",
            "web_link": "链接",
            "page": "页面",
            "scorm": "SCORM",
            "h5_courseware": "H5课件",
        }
        for index, item in enumerate(self.courseware_items):
            tree_id = f"courseware-{index}"
            permission_tag = "downloadable" if item.download_status == "可下载" else "blocked"
            self.courseware_tree.insert(
                "",
                "end",
                iid=tree_id,
                tags=(permission_tag,),
                values=(
                    item.module_name,
                    item.syllabus_name or "-",
                    item.activity_title,
                    item.filename or "-",
                    type_names.get(item.activity_type, item.media_type or item.activity_type or "-"),
                    format_size(item.size) if item.size else "-",
                    normalize_time(item.published_at),
                    item.download_status,
                    item.upload_status or "-",
                ),
            )
            self.courseware_items_by_tree_id[tree_id] = item

    def _set_grade_records(self, records: list[GradeRecord]):
        self.grade_records = records
        term_names = []
        for record in records:
            if record.term_name not in term_names:
                term_names.append(record.term_name)
        self.grade_term_combo.configure(values=("全部", *term_names))
        if records:
            latest_record = max(records, key=lambda record: record.term_code)
            self.grade_term_var.set(latest_record.term_name)
        else:
            self.grade_term_var.set("全部")
        self._refresh_grade_tree()
        self.log(f"成绩查询完成，共 {len(records)} 条。")

    def _refresh_grade_tree(self):
        selected_term = self.grade_term_var.get()
        records = [
            record for record in self.grade_records
            if selected_term == "全部" or record.term_name == selected_term
        ]
        for item in self.grade_tree.get_children():
            self.grade_tree.delete(item)
        for record in records:
            self.grade_tree.insert(
                "",
                "end",
                values=(
                    record.term_name,
                    record.course_name,
                    record.course_code,
                    record.credit,
                    record.score,
                    record.grade_point,
                    record.course_nature,
                    record.assessment,
                    record.composition,
                ),
            )
        if self.grade_records:
            self.grade_summary_var.set(f"共 {len(self.grade_records)} 条；当前显示 {len(records)} 条")
        else:
            self.grade_summary_var.set("暂无已公布成绩")

    def _refresh_course_tree(self):
        records = self.course_records
        display_records = [
            record for record in records
            if not self.only_unsigned_var.get() or record.signed_status == "未签到"
        ]

        for item in self.course_tree.get_children():
            self.course_tree.delete(item)

        signed = sum(1 for record in records if record.signed_status == "已签到")
        unsigned = sum(1 for record in records if record.signed_status == "未签到")
        unknown = sum(1 for record in records if record.signed_status == "未知")
        no_rollcalls = sum(1 for record in records if record.signed_status == "无签到记录")

        for record in display_records:
            self.course_tree.insert(
                "",
                "end",
                values=(
                    record.course_title,
                    record.rollcall_time,
                    record.rollcall_type,
                    record.signed_status,
                    record.platform_status,
                    record.rollcall_id,
                    record.detail,
                ),
            )

        summary = f"共 {len(records)} 条；已签 {signed}；未签 {unsigned}；未知 {unknown}；无记录 {no_rollcalls}"
        if self.only_unsigned_var.get():
            summary += f"；当前显示 {len(display_records)} 条未签"
        self.course_summary_var.set(summary)

    def _tick_runtime(self):
        if self.started_at and self.monitor_worker and self.monitor_worker.is_alive():
            elapsed = int(time.time() - self.started_at)
            self.runtime_var.set(format_duration(elapsed))
        self.root.after(1000, self._tick_runtime)

    def log(self, message: str):
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        self.log_text.insert("end", f"[{timestamp}] {message}\n")
        self.log_text.see("end")

    def _on_close(self):
        self.stop_monitor()
        self.root.after(150, self.root.destroy)


def format_duration(seconds: int) -> str:
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def main():
    root = tk.Tk()
    try:
        style = ttk.Style(root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
    except Exception:
        pass
    DashboardApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
