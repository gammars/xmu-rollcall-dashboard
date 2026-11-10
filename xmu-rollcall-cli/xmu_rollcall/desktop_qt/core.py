"""Shared dashboard data models and TronClass query helpers."""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from ..utils import base_url, headers, retry_request


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
    course_id: str = ""
    end_time: str = ""
    signed_count: int = 0
    total_count: int = 0
    strategy_status: str = "待检查"


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
    end_time: str = "-"
    attendance: str = "-"
    number_code: str = "-"
    enrichment_error: bool = False
    raw_rollcall_time: str = ""


@dataclass
class StudentRollcallRecord:
    name: str
    user_no: str
    klass: str
    department: str
    rollcall_status: str
    rollcall_time: str
    distance: str
    status_detail: str


@dataclass
class RollcallRoster:
    rollcall_id: str
    title: str
    status: str
    students: list[StudentRollcallRecord]
    limited: bool = False


@dataclass
class RollcallSnapshot:
    rollcall_id: str
    course_id: str
    course_title: str
    rollcall_time: str
    utc_date: str
    end_time: str
    status: str
    rollcall_type: str
    number_code: str
    signed_count: int
    total_count: int
    student_rollcalls: list[dict]
    raw: dict

    @property
    def ended(self) -> bool:
        if self.status.lower() in {"finished", "ended", "closed", "expired", "cancelled", "canceled"}:
            return True
        parsed = parse_datetime(self.end_time)
        return bool(parsed and parsed <= datetime.now(timezone.utc))

    @property
    def attendance_ratio(self) -> float | None:
        return self.signed_count / self.total_count if self.total_count else None


class MonitorWorker(threading.Thread):
    """Poll rollcall events in a background thread and emit tuple events."""

    def __init__(self, session, emit, stop_event: threading.Event, interval: int):
        super().__init__(daemon=True)
        self.session = session
        self.emit = emit
        self.stop_event = stop_event
        self.interval = max(1, int(interval or DEFAULT_POLL_INTERVAL_SECONDS))
        self.query_count = 0
        self.seen_rollcall_ids: set[str] = set()
        self.last_payload = {"rollcalls": []}

    def run(self):
        self.emit(("monitor_status", "运行中"))
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
                self.emit(("poll", self.query_count, time.time(), len(data.get("rollcalls", []))))

                if data != self.last_payload:
                    self.last_payload = data
                    for rollcall in data.get("rollcalls", []):
                        rollcall_id = str(rollcall.get("rollcall_id", ""))
                        if not rollcall_id or rollcall_id in self.seen_rollcall_ids:
                            continue
                        self.seen_rollcall_ids.add(rollcall_id)
                        self.emit(("rollcall", build_rollcall_event(rollcall)))
            except Exception as exc:
                self.emit(("error", f"轮询失败：{exc}"))

            self.stop_event.wait(self.interval)
        self.emit(("monitor_status", "已停止"))


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
        course_id=str(first_value(rollcall, ("course_id",), "")),
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
    if isinstance(raw_semester, dict):
        semester_code = str(raw_semester.get("code") or "")
    else:
        semester_code = str(raw_semester or course.get("term") or "")

    if semester == "第一学期":
        return semester_code.endswith("-1")
    if semester == "第二学期":
        return semester_code.endswith("-2")
    if semester == "第三学期":
        return semester_code.endswith("-3")
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


def parse_datetime(value) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone(timedelta(hours=8)))
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def rollcall_utc_date(value) -> str:
    parsed = parse_datetime(value)
    if not parsed:
        raise ValueError(f"无效的签到时间：{value!r}")
    return parsed.date().isoformat()


SIGNED_STATUSES = {"on_call", "on_call_fine", "present", "signed", "attended", "fine", "done", "late"}
ABSENT_STATUSES = {"absent", "missed", "not_signed", "unanswered"}


def timetable_rollcalls_from_payload(payload) -> list[dict]:
    if not isinstance(payload, dict):
        raise RuntimeError("timetable_rollcalls 未返回有效 JSON 对象")
    rows = payload.get("rollcalls")
    if not isinstance(rows, list):
        raise RuntimeError("timetable_rollcalls 响应缺少 rollcalls 数组")
    return [row for row in rows if isinstance(row, dict)]


def fetch_timetable_rollcalls(session, course_id: str, utc_date: str) -> list[dict]:
    response = session.get(
        f"{base_url}/api/timetable_rollcalls",
        params={"course_ids": str(course_id), "rollcall_date": str(utc_date)},
        headers=headers,
        timeout=20,
    )
    response.raise_for_status()
    try:
        return timetable_rollcalls_from_payload(response.json())
    except ValueError as exc:
        raise RuntimeError("timetable_rollcalls 未返回 JSON 数据") from exc


def snapshot_from_timetable_row(row: dict, fallback_course_id: str = "") -> RollcallSnapshot:
    students = row.get("student_rollcalls")
    if not isinstance(students, list):
        students = []
    students = [item for item in students if isinstance(item, dict)]
    signed_count = sum(
        str(item.get("status") or "").strip().lower() in SIGNED_STATUSES
        for item in students
    )
    rollcall_time = str(first_value(row, ("rollcall_time", "created_at", "start_time"), ""))
    course = row.get("course") if isinstance(row.get("course"), dict) else {}
    course_id = str(first_value(course, ("id", "course_id"), fallback_course_id))
    code = row.get("number_code")
    return RollcallSnapshot(
        rollcall_id=str(first_value(row, ("id", "rollcall_id"), "")),
        course_id=course_id,
        course_title=str(first_value(course, ("name", "title"), "")),
        rollcall_time=rollcall_time,
        utc_date=rollcall_utc_date(rollcall_time),
        end_time=str(first_value(row, ("end_time", "ended_at", "deadline"), "")),
        status=str(first_value(row, ("status", "state"), "unknown")),
        rollcall_type=normalize_rollcall_type(row),
        number_code="" if code in (None, "") else str(code),
        signed_count=signed_count,
        total_count=len(students),
        student_rollcalls=students,
        raw=row,
    )


def fetch_rollcall_snapshot(session, rollcall_id: str) -> RollcallSnapshot:
    detail_response = session.get(f"{base_url}/api/rollcall/{rollcall_id}", headers=headers, timeout=20)
    detail_response.raise_for_status()
    try:
        detail = detail_response.json()
    except ValueError as exc:
        raise RuntimeError("签到详情接口未返回 JSON 数据") from exc
    if not isinstance(detail, dict):
        raise RuntimeError("签到详情接口未返回有效 JSON 对象")
    course = detail.get("course") if isinstance(detail.get("course"), dict) else {}
    course_id = str(first_value(detail, ("course_id",), first_value(course, ("id", "course_id"), "")))
    rollcall_time = first_value(detail, ("rollcall_time", "created_at", "start_time"), "")
    if not course_id or not rollcall_time:
        raise RuntimeError("签到详情缺少 course_id 或 rollcall_time")
    rows = fetch_timetable_rollcalls(session, course_id, rollcall_utc_date(rollcall_time))
    match = next((row for row in rows if str(first_value(row, ("id", "rollcall_id"), "")) == str(rollcall_id)), None)
    if match is None:
        raise RuntimeError(f"timetable_rollcalls 中未找到签到 ID {rollcall_id}")
    return snapshot_from_timetable_row(match, course_id)


def snapshot_self_status(snapshot: RollcallSnapshot, profile_user_id: str) -> str:
    for item in snapshot.student_rollcalls:
        if str(item.get("student_id") or "") != str(profile_user_id):
            continue
        status = str(item.get("status") or "").lower()
        if status in SIGNED_STATUSES:
            return "已签到"
        if status in ABSENT_STATUSES:
            return "未签到"
        return normalize_student_rollcall_status(status)
    return "未知"


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


def display_nested_value(value, keys: tuple[str, ...] = ("name", "title", "code")) -> str:
    if isinstance(value, dict):
        return str(first_value(value, keys, "-") or "-")
    if isinstance(value, list):
        parts = [display_nested_value(item, keys) for item in value]
        return "、".join(part for part in parts if part != "-") or "-"
    return str(value if value not in (None, "") else "-")


def normalize_student_rollcall_status(value) -> str:
    raw = str(value or "unknown").strip().lower()
    labels = {
        "on_call": "已签到",
        "on_call_fine": "已签到",
        "present": "已签到",
        "signed": "已签到",
        "attended": "已签到",
        "fine": "已签到",
        "absent": "未签到",
        "missed": "未签到",
        "not_signed": "未签到",
        "unanswered": "未签到",
        "late": "迟到",
        "leave": "请假",
        "excused": "请假",
    }
    return labels.get(raw, str(value or "未知"))


def normalize_distance(value) -> str:
    if value in (None, ""):
        return "-"
    if isinstance(value, (int, float)):
        return f"{value:g} 米"
    text = str(value).strip()
    if not text:
        return "-"
    try:
        return f"{float(text):g} 米"
    except ValueError:
        return text


def parse_rollcall_roster(rollcall_id: str, payload) -> RollcallRoster:
    if not isinstance(payload, dict):
        raise RuntimeError("签到名单接口未返回有效 JSON 对象")

    raw_students = unwrap_list(payload, ("student_rollcalls", "students", "data", "items", "list", "results"))
    students = []
    for raw in raw_students:
        if not isinstance(raw, dict):
            continue
        students.append(StudentRollcallRecord(
            name=str(first_value(raw, ("name", "user_name", "student_name", "nickname"), "-") or "-"),
            user_no=str(first_value(raw, ("user_no", "student_no", "number", "username"), "-") or "-"),
            klass=display_nested_value(first_value(raw, ("klass", "class", "class_name"), "-")),
            department=display_nested_value(first_value(raw, ("department", "department_name", "college"), "-")),
            rollcall_status=normalize_student_rollcall_status(
                first_value(raw, ("rollcall_status", "status", "state"), "unknown")
            ),
            rollcall_time=normalize_time(first_value(raw, ("updated_at", "answered_at", "submitted_at"), "")),
            distance=normalize_distance(first_value(raw, ("distance", "distance_meters"), "")),
            status_detail=display_nested_value(first_value(raw, ("status_detail", "comment", "grade"), "-")),
        ))

    return RollcallRoster(
        rollcall_id=str(rollcall_id),
        title=str(first_value(payload, ("title", "name"), "签到名单") or "签到名单"),
        status=str(first_value(payload, ("status", "state"), "unknown") or "unknown"),
        students=students,
    )


def fetch_rollcall_roster(session, rollcall_id: str) -> RollcallRoster:
    if not rollcall_id or rollcall_id == "-":
        raise RuntimeError("该记录没有有效的签到 ID")
    try:
        response = session.get(
            f"{base_url}/api/rollcall/{rollcall_id}/student_rollcalls",
            headers=headers,
            timeout=20,
        )
        if response.status_code in (401, 403):
            raise RuntimeError("登录态已失效或当前账号无权查看签到名单")
        response.raise_for_status()
        roster = parse_rollcall_roster(rollcall_id, response.json())
        if roster.students:
            return roster
    except Exception:
        pass

    snapshot = fetch_rollcall_snapshot(session, rollcall_id)
    students = [StudentRollcallRecord(
        name=f"学生 #{item.get('student_id', '-')}",
        user_no="-",
        klass="-",
        department="-",
        rollcall_status=normalize_student_rollcall_status(item.get("status")),
        rollcall_time="-",
        distance="-",
        status_detail="平台未返回姓名",
    ) for item in snapshot.student_rollcalls]
    return RollcallRoster(
        rollcall_id=rollcall_id,
        title=f"{snapshot.course_title or '签到'}（平台未返回姓名）",
        status=snapshot.status,
        students=students,
        limited=True,
    )


def fetch_number_code(session, rollcall_id: str) -> str:
    return fetch_rollcall_snapshot(session, rollcall_id).number_code


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
            raw_rollcall_time = str(first_value(rollcall, ("rollcall_time", "created_at", "start_time", "time"), ""))
            records.append(CourseRollcallRecord(
                course_id=course["id"],
                course_title=course["title"],
                rollcall_id=rollcall_id or "-",
                rollcall_time=normalize_time(raw_rollcall_time),
                rollcall_type=normalize_rollcall_type(rollcall),
                signed_status=signed_status,
                platform_status=platform_status,
                detail="",
                raw_rollcall_time=raw_rollcall_time,
            ))

    enrich_course_rollcall_records(session, records, student_id)
    source = f"课程接口：{course_endpoint}；筛选：{academic_year or '全部学年'} / {semester or '全部'}"
    if endpoint_hits:
        source += f"；签到接口示例：{endpoint_hits[0]}"
    return records, source


def enrich_course_rollcall_records(session, records: list[CourseRollcallRecord], profile_user_id: str, max_workers: int = 6) -> None:
    """Enrich course history in bounded `(course_id, UTC date)` batches."""
    groups: dict[tuple[str, str], list[CourseRollcallRecord]] = {}
    for record in records:
        if record.rollcall_id == "-":
            continue
        raw_time = record.raw_rollcall_time or record.rollcall_time
        try:
            key = (record.course_id, rollcall_utc_date(raw_time))
        except ValueError:
            record.enrichment_error = True
            record.detail = "无法解析签到时间，扩展数据未加载"
            continue
        groups.setdefault(key, []).append(record)

    if not groups:
        return
    with ThreadPoolExecutor(max_workers=min(max_workers, len(groups))) as executor:
        futures = {
            executor.submit(fetch_timetable_rollcalls, session, course_id, utc_date): (course_id, utc_date)
            for course_id, utc_date in groups
        }
        for future in as_completed(futures):
            key = futures[future]
            group = groups[key]
            try:
                rows = future.result()
            except Exception as exc:
                for record in group:
                    if not enrich_course_record_from_detail(session, record, profile_user_id):
                        record.enrichment_error = True
                        record.detail = f"扩展数据失败：{exc}"
                continue
            by_id = {str(first_value(row, ("id", "rollcall_id"), "")): row for row in rows}
            for record in group:
                row = by_id.get(str(record.rollcall_id))
                if row is None:
                    if not enrich_course_record_from_detail(session, record, profile_user_id):
                        record.enrichment_error = True
                        record.detail = "扩展数据中未找到该签到 ID，详情接口也不可用"
                    continue
                try:
                    snapshot = snapshot_from_timetable_row(row, record.course_id)
                except Exception as exc:
                    record.enrichment_error = True
                    record.detail = f"扩展数据解析失败：{exc}"
                    continue
                record.end_time = normalize_time(snapshot.end_time)
                record.attendance = f"{snapshot.signed_count}/{snapshot.total_count}" if snapshot.total_count else "-"
                record.number_code = snapshot.number_code if snapshot.rollcall_type == "数字签到" and snapshot.number_code else "-"
                record.platform_status = snapshot.status
                record.rollcall_type = snapshot.rollcall_type
                record.signed_status = snapshot_self_status(snapshot, profile_user_id)


def enrich_course_record_from_detail(session, record: CourseRollcallRecord, profile_user_id: str) -> bool:
    """Fallback for non-timetable `another` records; never reads a number code."""
    payload = fetch_student_rollcall_detail(session, record.rollcall_id)
    if not isinstance(payload, dict):
        return False
    students = unwrap_list(payload, ("student_rollcalls", "students", "data", "items", "list"))
    students = [item for item in students if isinstance(item, dict)]
    if not students:
        return False
    signed = sum(
        str(first_value(item, ("status", "rollcall_status", "state"), "")).lower() in SIGNED_STATUSES
        for item in students
    )
    record.attendance = f"{signed}/{len(students)}"
    record.end_time = normalize_time(first_value(payload, ("end_time", "ended_at", "deadline"), ""))
    record.platform_status = str(first_value(payload, ("status", "state"), record.platform_status))
    record.rollcall_type = normalize_rollcall_type(payload)
    record.number_code = "-"
    own = next((item for item in students if str(item.get("student_id") or "") == str(profile_user_id)), None)
    if own:
        own_status = str(first_value(own, ("status", "rollcall_status", "state"), "")).lower()
        if own_status in SIGNED_STATUSES:
            record.signed_status = "已签到"
        elif own_status in ABSENT_STATUSES:
            record.signed_status = "未签到"
        else:
            record.signed_status = normalize_student_rollcall_status(own_status)
    record.detail = "到课人数来自签到详情（新接口未返回该记录）"
    record.enrichment_error = False
    return True


def format_duration(seconds: int) -> str:
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"
