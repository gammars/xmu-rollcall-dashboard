"""Thread-safe application service used by the local web control panel."""

from __future__ import annotations

import os
import threading
import time
from collections import deque
from dataclasses import asdict
from datetime import datetime

import requests
from xmulogin import xmulogin

from .. import __version__
from ..config import (
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
from ..desktop_qt.core import ROLLCALLS_URL, build_rollcall_event, fetch_rollcall_snapshot
from ..courseware import fetch_courses
from ..utils import headers, load_session, save_session, verify_session
from ..verify import send_code, send_radar


class WebService:
    """Own the authenticated session and exactly one monitoring thread."""

    def __init__(self, poll_interval: int = 2, snapshot_interval: int = 5):
        self.poll_interval = max(1, int(poll_interval))
        self.snapshot_interval = max(2, int(snapshot_interval))
        self.lock = threading.RLock()
        self.session: requests.Session | None = None
        self.account: dict | None = None
        self.profile: dict = {}
        self.monitor_thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.auto_submit = False
        self.events: dict[str, dict] = {}
        self.event_order: deque[str] = deque(maxlen=100)
        self.pending_ids: set[str] = set()
        self.attempted_ids: set[str] = set()
        self.logs: deque[dict] = deque(maxlen=200)
        self.query_count = 0
        self.last_check = ""
        self.last_error = ""
        self._log("info", "Web 控制台已启动，自动提交默认关闭。")

    def _log(self, level: str, message: str) -> None:
        with self.lock:
            self.logs.appendleft({
                "time": datetime.now().strftime("%H:%M:%S"),
                "level": level,
                "message": str(message),
            })

    @property
    def monitoring(self) -> bool:
        return bool(self.monitor_thread and self.monitor_thread.is_alive())

    def login(self, username: str, password: str) -> dict:
        username = username.strip()
        if not username or not password:
            raise ValueError("请输入学号和密码")
        session = xmulogin(type=3, username=username, password=password)
        if not session:
            raise RuntimeError("登录失败，请检查学号或密码")
        profile = verify_session(session)
        if not profile:
            session.close()
            raise RuntimeError("登录后无法验证畅课会话")

        config = load_config()
        account = next((item for item in get_all_accounts(config) if item.get("username") == username), None)
        if account is None:
            account_id = add_account(config, username, password, profile.get("name") or username)
            account = next(item for item in get_all_accounts(config) if item.get("id") == account_id)
        else:
            account["password"] = password
            account["name"] = profile.get("name") or username
        set_current_account(config, account["id"])
        set_rollcall_settings(account, get_rollcall_settings(account))
        save_config(config)
        save_session(session, get_cookies_path(account["id"]))
        self._replace_session(session, account, profile)
        self._log("success", f"登录成功：{account.get('name') or username}")
        return self.public_status()

    def restore(self) -> dict:
        config = load_config()
        account = get_current_account(config)
        if not account:
            raise RuntimeError("没有保存的账号，请先登录")
        session = requests.Session()
        cookie_path = get_cookies_path(account.get("id", 1))
        profile = verify_session(session) if load_session(session, cookie_path) else {}
        if not profile:
            session.close()
            username = str(account.get("username") or "")
            password = str(account.get("password") or "")
            if not username or not password:
                raise RuntimeError("Cookie 已失效，且没有保存的完整账号密码")
            session = xmulogin(type=3, username=username, password=password)
            profile = verify_session(session) if session else {}
            if not session or not profile:
                if session:
                    session.close()
                raise RuntimeError("恢复登录失败，请手动登录")
        save_session(session, cookie_path)
        self._replace_session(session, account, profile)
        self._log("success", f"已恢复登录：{account.get('name') or account.get('username')}")
        return self.public_status()

    def _replace_session(self, session: requests.Session, account: dict, profile: dict) -> None:
        self.stop_monitor()
        with self.lock:
            old = self.session
            self.session = session
            self.account = dict(account)
            self.profile = dict(profile)
            self.events.clear()
            self.event_order.clear()
            self.pending_ids.clear()
            self.attempted_ids.clear()
            self.query_count = 0
            self.last_check = ""
            self.last_error = ""
            self.auto_submit = False
        if old and old is not session:
            old.close()

    def disconnect(self) -> dict:
        self.stop_monitor()
        with self.lock:
            session = self.session
            self.session = None
            self.account = None
            self.profile = {}
            self.auto_submit = False
        if session:
            session.close()
        self._log("info", "当前 Web 会话已断开；本地 Cookie 未删除。")
        return self.public_status()

    def start_monitor(self, auto_submit: bool = False) -> dict:
        with self.lock:
            if not self.session or not self.account:
                raise RuntimeError("请先登录或恢复会话")
            self.auto_submit = bool(auto_submit)
            if self.monitoring:
                self._log("info", f"监听设置已更新，自动提交：{'开启' if self.auto_submit else '关闭'}。")
                return self.public_status()
            self.stop_event = threading.Event()
            self.monitor_thread = threading.Thread(target=self._monitor_loop, name="xmu-web-monitor", daemon=True)
            self.monitor_thread.start()
        self._log("success", f"监听已启动，自动提交：{'开启' if auto_submit else '关闭'}。")
        return self.public_status()

    def stop_monitor(self) -> dict:
        with self.lock:
            thread = self.monitor_thread
            self.stop_event.set()
        if thread and thread is not threading.current_thread():
            thread.join(timeout=max(3, self.poll_interval + 1))
        with self.lock:
            if thread is self.monitor_thread and not (thread and thread.is_alive()):
                self.monitor_thread = None
            self.auto_submit = False
        if thread:
            self._log("info", "监听已停止，自动提交已关闭。")
        return self.public_status()

    def set_settings(self, threshold: int, auto_submit: bool | None = None) -> dict:
        threshold = int(threshold)
        if not 1 <= threshold <= 100:
            raise ValueError("到课阈值必须在 1–100 之间")
        config = load_config()
        account = get_current_account(config)
        if not account:
            raise RuntimeError("没有当前账号")
        settings = get_rollcall_settings(account)
        settings["attendance_threshold_enabled"] = True
        settings["attendance_threshold_percent"] = threshold
        set_rollcall_settings(account, settings)
        save_config(config)
        with self.lock:
            if self.account and str(self.account.get("id")) == str(account.get("id")):
                self.account["rollcall_settings"] = dict(account["rollcall_settings"])
            if auto_submit is not None:
                self.auto_submit = bool(auto_submit) if self.monitoring else False
        self._log("info", f"到课阈值已更新为 {threshold}%。")
        return self.public_status()

    def list_courses(self) -> list[dict]:
        with self.lock:
            session = self.session
        if not session:
            raise RuntimeError("请先登录")
        courses, _source = fetch_courses(session)
        return [
            {"course_id": item.course_id, "title": item.title, "term": item.term}
            for item in courses
        ]

    def bind_location(self, course_id: str, course_title: str, preset_id: str) -> dict:
        course_id = str(course_id).strip()
        preset_id = str(preset_id).strip()
        if not course_id or not preset_id:
            raise ValueError("请选择课程和地点")
        config = load_config()
        account = get_current_account(config)
        if not account:
            raise RuntimeError("没有当前账号")
        preset = next(
            (item for item in config.get("location_presets", []) if str(item.get("id")) == preset_id),
            None,
        )
        if not preset:
            raise ValueError("所选地点不存在")
        settings = get_rollcall_settings(account)
        settings["course_location_bindings"][course_id] = {
            "preset_id": preset_id,
            "course_title": str(course_title).strip() or course_id,
        }
        set_rollcall_settings(account, settings)
        save_config(config)
        with self.lock:
            if self.account and str(self.account.get("id")) == str(account.get("id")):
                self.account["rollcall_settings"] = dict(account["rollcall_settings"])
        self._log("success", f"已将《{course_title or course_id}》绑定到 {preset['name']}。")
        return self.public_status()

    def _monitor_loop(self) -> None:
        next_snapshot_at = 0.0
        while not self.stop_event.is_set():
            with self.lock:
                session = self.session
            if not session:
                break
            try:
                response = session.get(ROLLCALLS_URL, headers=headers, timeout=20)
                response.raise_for_status()
                payload = response.json()
                rows = payload.get("rollcalls", []) if isinstance(payload, dict) else []
                if not isinstance(rows, list):
                    raise RuntimeError("签到监听接口未返回 rollcalls 数组")
                now = time.monotonic()
                with self.lock:
                    self.query_count += 1
                    self.last_check = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    self.last_error = ""
                for row in rows:
                    if isinstance(row, dict):
                        self._register_event(row)
                if now >= next_snapshot_at:
                    for event_id in tuple(self.pending_ids):
                        if self.stop_event.is_set():
                            break
                        self._refresh_event(event_id)
                    next_snapshot_at = now + self.snapshot_interval
            except Exception as exc:
                with self.lock:
                    self.last_error = str(exc)
                self._log("error", f"监听失败：{exc}")
            self.stop_event.wait(self.poll_interval)

    def _register_event(self, row: dict) -> None:
        event = build_rollcall_event(row)
        event_id = event.rollcall_id
        if not event_id:
            return
        with self.lock:
            if event_id in self.events:
                return
            data = asdict(event)
            data.update({
                "detected_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "attendance": "-",
                "threshold_percent": self._threshold(),
            })
            self.events[event_id] = data
            self.event_order.appendleft(event_id)
            self.pending_ids.add(event_id)
            if event.rollcall_type not in {"数字签到", "雷达签到"}:
                data["strategy_status"] = "仅提醒"
                data["result"] = "需手动处理"
        self._log("warning", f"发现{event.rollcall_type}：{event.course_title}（{event_id}）")
        self._refresh_event(event_id)

    def _refresh_event(self, event_id: str) -> None:
        with self.lock:
            if event_id not in self.pending_ids or not self.session:
                return
            session = self.session
        try:
            snapshot = fetch_rollcall_snapshot(session, event_id)
        except Exception as exc:
            with self.lock:
                event = self.events.get(event_id)
                if event:
                    event["strategy_status"] = "扩展数据异常"
                    event["detail"] = str(exc)
            self._log("error", f"签到 {event_id} 快照读取失败：{exc}")
            return

        with self.lock:
            event = self.events.get(event_id)
            if not event:
                return
            event.update({
                "course_id": snapshot.course_id,
                "course_title": snapshot.course_title or event["course_title"],
                "status": snapshot.status,
                "end_time": snapshot.end_time,
                "number_code": snapshot.number_code,
                "signed_count": snapshot.signed_count,
                "total_count": snapshot.total_count,
                "attendance": f"{snapshot.signed_count}/{snapshot.total_count}" if snapshot.total_count else "-",
            })
            if event["rollcall_type"] not in {"数字签到", "雷达签到"}:
                event["strategy_status"] = "仅提醒"
                event["result"] = "需手动处理"
                event["detail"] = "已读取到课人数，此类型不自动提交"
                self.pending_ids.discard(event_id)
                return
            threshold = self._threshold()
            event["threshold_percent"] = threshold
            if snapshot.ended:
                event["strategy_status"] = "平台已结束"
                event["result"] = "已结束"
                self.pending_ids.discard(event_id)
                return
            if not snapshot.total_count:
                event["strategy_status"] = "无有效名单"
                event["result"] = "等待数据"
                event["detail"] = "平台未返回学生状态，禁止自动提交"
                return
            percent = snapshot.signed_count * 100 / snapshot.total_count
            event["attendance_percent"] = round(percent, 1)
            if percent < threshold:
                event["strategy_status"] = f"等待 {threshold}%"
                event["result"] = "等待阈值"
                event["detail"] = f"当前 {percent:.1f}%（{snapshot.signed_count}/{snapshot.total_count}）"
                return
            if event["rollcall_type"] == "数字签到" and not snapshot.number_code:
                event["strategy_status"] = "签到码为空"
                event["result"] = "不可提交"
                return
            location = self._location_for_event(event)
            if event["rollcall_type"] == "雷达签到" and not location:
                event["strategy_status"] = "未配置地点"
                event["result"] = "未配置地点"
                return
            event["strategy_status"] = "已达阈值"
            event["result"] = "可提交"
            event["detail"] = f"当前 {percent:.1f}%（{snapshot.signed_count}/{snapshot.total_count}）"
            should_submit = self.auto_submit and event_id not in self.attempted_ids

        if should_submit:
            self._submit_event(event_id, snapshot.number_code, location)

    def _submit_event(self, event_id: str, number_code: str, location: dict | None) -> None:
        with self.lock:
            if event_id in self.attempted_ids or not self.session:
                return
            event = self.events.get(event_id)
            if not event:
                return
            self.attempted_ids.add(event_id)
            event["result"] = "提交中"
            session = self.session
            rollcall_type = event["rollcall_type"]
        try:
            if rollcall_type == "数字签到":
                ok = send_code(session, event_id, number_code)
            elif rollcall_type == "雷达签到" and location:
                ok = send_radar(session, event_id, location["latitude"], location["longitude"], location["accuracy"])
            else:
                ok = False
        except Exception as exc:
            ok = False
            detail = str(exc)
        else:
            detail = "提交成功" if ok else "提交失败（不会自动重试）"
        with self.lock:
            event = self.events.get(event_id)
            if event:
                event["result"] = "已签到" if ok else "提交失败"
                event["detail"] = detail
            self.pending_ids.discard(event_id)
        self._log("success" if ok else "error", f"签到 {event_id}：{detail}")

    def _threshold(self) -> int:
        account = self.account or {}
        return int(get_rollcall_settings(account)["attendance_threshold_percent"])

    def _location_for_event(self, event: dict) -> dict | None:
        account = self.account or {}
        binding = get_rollcall_settings(account)["course_location_bindings"].get(str(event.get("course_id") or ""))
        if not isinstance(binding, dict):
            return None
        preset_id = str(binding.get("preset_id") or "")
        return next((item for item in load_config().get("location_presets", []) if str(item.get("id")) == preset_id), None)

    def public_status(self) -> dict:
        with self.lock:
            account = self.account or {}
            config = load_config()
            settings = get_rollcall_settings(account)
            events = [dict(self.events[event_id]) for event_id in self.event_order if event_id in self.events]
            for event in events:
                event.pop("raw", None)
            bindings = settings.get("course_location_bindings", {})
            return {
                "version": __version__,
                "logged_in": bool(self.session and self.account),
                "account": {
                    "name": account.get("name") or self.profile.get("name") or "",
                    "username": account.get("username") or "",
                } if account else None,
                "monitoring": self.monitoring,
                "auto_submit": self.auto_submit if self.monitoring else False,
                "threshold": settings["attendance_threshold_percent"],
                "query_count": self.query_count,
                "last_check": self.last_check,
                "last_error": self.last_error,
                "events": events,
                "logs": list(self.logs),
                "locations": config.get("location_presets", []),
                "bindings": bindings,
                "server_pid": os.getpid(),
            }
