"""Read and download courseware available to the logged-in LNT account."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from .utils import base_url, headers


COURSE_ENDPOINTS = (
    "/api/my-courses?per_page=1000",
    "/api/courses?role=student&per_page=1000",
    "/api/courses?course_role=student&per_page=1000",
    "/api/my/courses?per_page=1000",
    "/api/courses?per_page=1000",
    "/api/courses",
)

API_HEADERS = {**headers, "Accept": "application/json, text/plain, */*"}
WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}


@dataclass(frozen=True)
class CourseSummary:
    course_id: str
    title: str
    term: str = ""

    @property
    def display_name(self) -> str:
        return f"{self.title}  [{self.term}]" if self.term else self.title


@dataclass(frozen=True)
class CoursewareItem:
    course_id: str
    activity_id: str
    activity_title: str
    activity_type: str
    module_name: str
    syllabus_name: str
    upload_id: str
    reference_id: str
    filename: str
    size: int
    media_type: str
    published_at: str
    upload_status: str
    allow_download: bool

    @property
    def download_status(self) -> str:
        if not self.upload_id:
            return "无附件"
        if self.upload_status != "ready":
            return "文件处理中"
        return "可下载"


def _first_value(data: dict, keys: tuple[str, ...], default=""):
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return value
    return default


def _unwrap_list(payload, keys: tuple[str, ...]) -> list:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            nested = _unwrap_list(value, keys)
            if nested:
                return nested
    for value in payload.values():
        if isinstance(value, dict):
            nested = _unwrap_list(value, keys)
            if nested:
                return nested
    return []


def _get_json(session, endpoint: str, label: str):
    response = session.get(f"{base_url}{endpoint}", headers=API_HEADERS, timeout=30)
    if response.status_code in (401, 403):
        raise RuntimeError(f"{label}失败：登录态已失效或当前账号无权访问")
    response.raise_for_status()
    try:
        return response.json()
    except ValueError as exc:
        raise RuntimeError(f"{label}失败：平台未返回 JSON 数据") from exc


def fetch_courses(session) -> tuple[list[CourseSummary], str]:
    errors = []
    for endpoint in COURSE_ENDPOINTS:
        try:
            payload = _get_json(session, endpoint, "课程列表读取")
        except Exception as exc:
            errors.append(f"{endpoint}: {exc}")
            continue

        raw_courses = _unwrap_list(payload, ("courses", "data", "items", "list", "results"))
        courses = []
        for raw in raw_courses:
            if not isinstance(raw, dict):
                continue
            course_id = _first_value(raw, ("id", "course_id", "courseId", "cid", "uuid"))
            title = _first_value(raw, ("name", "title", "course_title", "course_name", "display_name"))
            if not course_id or not title:
                continue
            term = _first_value(raw, ("term_name", "semester_name", "term", "semester", "academic_year"), "")
            if isinstance(term, dict):
                term = _first_value(term, ("name", "code", "id"), "")
            courses.append(CourseSummary(str(course_id), str(title), str(term)))

        if courses:
            courses.sort(key=lambda course: (course.term, course.title), reverse=True)
            return courses, endpoint
    raise RuntimeError("课程列表接口读取失败：" + "；".join(errors[:4]))


def fetch_courseware(session, course_id: str) -> list[CoursewareItem]:
    activities = _get_json(
        session,
        f"/api/course/{course_id}/courseware-activities",
        "课件活动读取",
    )
    modules_payload = _get_json(session, f"/api/courses/{course_id}/modules", "课程章节读取")
    modules = _unwrap_list(modules_payload, ("modules", "data", "items", "list"))

    module_names: dict[str, str] = {}
    syllabus_names: dict[str, str] = {}
    for module in modules:
        if not isinstance(module, dict):
            continue
        module_id = str(module.get("id") or "")
        module_names[module_id] = str(module.get("name") or "未分组")
        for syllabus in module.get("syllabuses") or []:
            if isinstance(syllabus, dict):
                syllabus_names[str(syllabus.get("id") or syllabus.get("syllabus_id") or "")] = str(
                    syllabus.get("summary") or syllabus.get("name") or ""
                )

    raw_activities = activities if isinstance(activities, list) else _unwrap_list(
        activities, ("activities", "data", "items", "list")
    )
    items: list[CoursewareItem] = []
    for activity in raw_activities:
        if not isinstance(activity, dict) or not activity.get("id"):
            continue
        if activity.get("type") == "homework":
            continue
        detail = _get_json(session, f"/api/activities/{activity['id']}", "课件详情读取")
        uploads = detail.get("uploads") or []
        common = {
            "course_id": str(course_id),
            "activity_id": str(activity["id"]),
            "activity_title": str(detail.get("title") or activity.get("title") or "未命名课件"),
            "activity_type": str(detail.get("type") or activity.get("type") or "material"),
            "module_name": module_names.get(str(detail.get("module_id") or activity.get("module_id") or ""), "未分组"),
            "syllabus_name": syllabus_names.get(str(detail.get("syllabus_id") or activity.get("syllabus_id") or ""), ""),
            "published_at": str((detail.get("data") or {}).get("publish_time") or detail.get("created_at") or ""),
        }
        if not uploads:
            items.append(CoursewareItem(
                **common,
                upload_id="",
                reference_id="",
                filename="",
                size=0,
                media_type="",
                upload_status="",
                allow_download=False,
            ))
            continue
        for upload in uploads:
            if not isinstance(upload, dict):
                continue
            items.append(CoursewareItem(
                **common,
                upload_id=str(upload.get("id") or ""),
                reference_id=str(upload.get("reference_id") or ""),
                filename=str(upload.get("name") or common["activity_title"]),
                size=int(upload.get("size") or 0),
                media_type=str(upload.get("type") or "file"),
                upload_status=str(upload.get("status") or ""),
                allow_download=bool(upload.get("allow_download")),
            ))

    items.sort(key=lambda item: (item.module_name, item.syllabus_name, item.published_at, item.activity_title))
    return items


def sanitize_filename(filename: str, fallback: str = "courseware") -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", filename).strip().rstrip(". ")
    if not cleaned:
        cleaned = fallback
    if cleaned.split(".", 1)[0].upper() in WINDOWS_RESERVED_NAMES:
        cleaned = f"_{cleaned}"
    return cleaned[:240]


def available_path(directory: Path, filename: str) -> Path:
    target = directory / sanitize_filename(filename)
    if not target.exists() and not target.with_name(target.name + ".part").exists():
        return target
    stem, suffix = target.stem, target.suffix
    number = 2
    while True:
        candidate = directory / f"{stem} ({number}){suffix}"
        if not candidate.exists() and not candidate.with_name(candidate.name + ".part").exists():
            return candidate
        number += 1


def download_courseware(session, item: CoursewareItem, destination: str | Path) -> Path:
    if not item.upload_id or item.upload_status != "ready":
        raise PermissionError(item.download_status)
    if not item.reference_id:
        raise RuntimeError("课件缺少 reference_id，无法获取签名下载地址")

    directory = Path(destination)
    directory.mkdir(parents=True, exist_ok=True)
    target = available_path(directory, item.filename or item.activity_title)
    partial = target.with_name(target.name + ".part")
    signed_payload = _get_json(
        session,
        f"/api/uploads/reference/{item.reference_id}/url",
        "签名下载地址读取",
    )
    signed_url = signed_payload.get("url") if isinstance(signed_payload, dict) else ""
    if not isinstance(signed_url, str) or not signed_url.startswith(("https://", "http://")):
        raise RuntimeError("平台未返回有效的签名下载地址")
    response = session.get(
        signed_url,
        headers={"User-Agent": headers["User-Agent"]},
        timeout=(20, 120),
        stream=True,
    )
    try:
        if response.status_code in (401, 403):
            raise PermissionError("平台拒绝下载该课件")
        response.raise_for_status()
        content_type = (response.headers.get("Content-Type") or "").lower()
        if "text/html" in content_type:
            raise RuntimeError("下载返回了登录页面，请重新登录后再试")
        with partial.open("wb") as file:
            for chunk in response.iter_content(chunk_size=1024 * 256):
                if chunk:
                    file.write(chunk)
        os.replace(partial, target)
        return target
    finally:
        response.close()
        if partial.exists():
            partial.unlink(missing_ok=True)


def format_size(size: int) -> str:
    value = float(size or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"
