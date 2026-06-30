"""XMU academic-affairs login and current-user grade queries."""

from __future__ import annotations

import base64
import json
import random
import re
import time
from dataclasses import dataclass

import requests
from Crypto.Cipher import AES


CAS_LOGIN_URL = "https://ids.xmu.edu.cn/authserver/login"
JW_BASE_URL = "https://jw.xmu.edu.cn"
JW_PORTAL_URL = f"{JW_BASE_URL}/new/index.html"
JW_GRADE_APP_ID = "4768574631264620"
JW_GRADE_ENDPOINT = f"{JW_BASE_URL}/jwapp/sys/cjcx/modules/cjcx/xscjcx.do"

CAS_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": CAS_LOGIN_URL,
}

AES_CHARS = "ABCDEFGHJKMNPQRSTWXYZabcdefhijkmnprstwxyz2345678"


@dataclass(frozen=True)
class GradeRecord:
    term_code: str
    term_name: str
    course_code: str
    course_name: str
    credit: str
    score: str
    grade_point: str
    course_nature: str
    assessment: str
    composition: str


def _random_string(length: int) -> str:
    return "".join(random.choice(AES_CHARS) for _ in range(length))


def _encrypt_password(password: str, salt: str) -> str:
    plaintext = _random_string(64) + password
    pad_length = 16 - len(plaintext.encode("utf-8")) % 16
    padded = plaintext.encode("utf-8") + bytes([pad_length]) * pad_length
    cipher = AES.new(salt.encode("utf-8"), AES.MODE_CBC, _random_string(16).encode("utf-8"))
    return base64.b64encode(cipher.encrypt(padded)).decode("ascii")


def _extract_hidden_value(html: str, field_id: str, field_name: str) -> str:
    patterns = (
        rf'id=["\']{re.escape(field_id)}["\'][^>]*value=["\']([^"\']+)',
        rf'name=["\']{re.escape(field_name)}["\'][^>]*value=["\']([^"\']+)',
    )
    for pattern in patterns:
        match = re.search(pattern, html, re.IGNORECASE)
        if match:
            return match.group(1)
    raise RuntimeError(f"统一认证页面缺少字段：{field_name}")


def _json_response(response: requests.Response, label: str) -> dict:
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(f"{label}未返回 JSON（HTTP {response.status_code}）") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{label}返回格式异常")
    return payload


def create_jw_session(username: str, password: str) -> tuple[requests.Session, str]:
    """Log into the JW portal and open the grade-query application."""
    session = requests.Session()
    login_page = session.get(
        CAS_LOGIN_URL,
        params={"service": JW_PORTAL_URL},
        headers=CAS_HEADERS,
        timeout=30,
    )
    login_page.raise_for_status()
    salt = _extract_hidden_value(login_page.text, "pwdEncryptSalt", "pwdEncryptSalt")
    execution = _extract_hidden_value(login_page.text, "execution", "execution")

    login_data = {
        "username": username,
        "password": _encrypt_password(password, salt),
        "captcha": "",
        "_eventId": "submit",
        "cllt": "userNameLogin",
        "dllt": "generalLogin",
        "lt": "",
        "execution": execution,
    }
    portal_page = session.post(
        CAS_LOGIN_URL,
        params={"service": JW_PORTAL_URL},
        headers=CAS_HEADERS,
        data=login_data,
        allow_redirects=True,
        timeout=30,
    )
    portal_page.raise_for_status()
    if not portal_page.url.startswith(JW_BASE_URL):
        raise RuntimeError("教务统一认证失败，请检查账号、密码或验证码要求。")

    json_headers = {
        "User-Agent": CAS_HEADERS["User-Agent"],
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": portal_page.url,
        "X-Requested-With": "XMLHttpRequest",
    }
    user_info_url = f"{JW_BASE_URL}/jsonp/userInfo.json"
    seed_response = session.get(
        user_info_url,
        params={"_": str(int(time.time() * 1000))},
        headers=json_headers,
        timeout=30,
    )
    seed_info = _json_response(seed_response, "教务门户初始化")

    if not seed_info.get("hasLogin"):
        portal_login = session.get(
            f"{JW_BASE_URL}/login",
            params={"service": JW_PORTAL_URL},
            headers={"User-Agent": CAS_HEADERS["User-Agent"], "Referer": portal_page.url},
            allow_redirects=True,
            timeout=30,
        )
        portal_login.raise_for_status()
        json_headers["Referer"] = portal_login.url
        user_response = session.get(
            user_info_url,
            params={"_": str(int(time.time() * 1000))},
            headers=json_headers,
            timeout=30,
        )
        user_info = _json_response(user_response, "教务门户登录校验")
    else:
        user_info = seed_info

    if not user_info.get("hasLogin"):
        raise RuntimeError("教务门户登录未生效，请稍后重试。")

    app_response = session.get(
        f"{JW_BASE_URL}/appShow",
        params={"appId": JW_GRADE_APP_ID},
        headers={"User-Agent": CAS_HEADERS["User-Agent"], "Referer": JW_PORTAL_URL},
        allow_redirects=True,
        timeout=30,
    )
    app_response.raise_for_status()
    if "/jwapp/sys/cjcx/" not in app_response.url:
        raise RuntimeError("无法打开教务系统的成绩查询应用。")
    return session, app_response.url


def _query_setting(term_code: str = "") -> list[dict]:
    settings: list[dict] = []
    if term_code:
        settings.append({
            "name": "XNXQDM",
            "value": term_code,
            "linkOpt": "and",
            "builder": "m_value_equal",
        })
    settings.extend((
        {
            "name": "SFYX",
            "caption": "是否有效",
            "linkOpt": "AND",
            "builderList": "cbl_m_List",
            "builder": "m_value_equal",
            "value": "1",
            "value_display": "是",
        },
        {
            "name": "SHOWMAXCJ",
            "caption": "显示最高成绩",
            "linkOpt": "AND",
            "builderList": "cbl_m_List",
            "builder": "m_value_equal",
            "value": "0",
            "value_display": "否",
        },
    ))
    return settings


def _display_value(row: dict, *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            if isinstance(value, float) and value.is_integer():
                return str(int(value))
            return str(value)
    return "-"


def _composition(row: dict) -> str:
    labels = (
        ("平时", "PSCJXS"),
        ("期中", "QZCJXS"),
        ("期末", "QMCJXS"),
        ("实验", "SYCJXS"),
        ("其他1", "QTCJ1XS"),
        ("其他2", "QTCJ2XS"),
        ("其他3", "QTCJ3XS"),
        ("其他4", "QTCJ4XS"),
        ("其他5", "QTCJ5XS"),
        ("其他6", "QTCJ6XS"),
        ("其他7", "QTCJ7XS"),
        ("其他8", "QTCJ8XS"),
        ("其他9", "QTCJ9XS"),
        ("其他10", "QTCJ10XS"),
    )
    parts = []
    for label, key in labels:
        value = row.get(key)
        if value in (None, "", 0, "0", "0.0"):
            continue
        text = str(value).rstrip("0").rstrip(".") if "." in str(value) else str(value)
        parts.append(f"{label}{text}%")
    return " / ".join(parts) or "-"


def normalize_grade(row: dict) -> GradeRecord:
    return GradeRecord(
        term_code=_display_value(row, "XNXQDM"),
        term_name=_display_value(row, "XNXQDM_DISPLAY", "XNXQDM"),
        course_code=_display_value(row, "KCH", "XSKCH"),
        course_name=_display_value(row, "KCM", "XSKCM"),
        credit=_display_value(row, "XF"),
        score=_display_value(row, "XSZCJMC", "DJCJMC", "ZCJ"),
        grade_point=_display_value(row, "XFJD", "JDF"),
        course_nature=_display_value(row, "KCXZDM_DISPLAY", "KCLBDM_DISPLAY"),
        assessment=_display_value(row, "KSLXDM_DISPLAY"),
        composition=_composition(row),
    )


def fetch_grade_records(username: str, password: str, term_code: str = "") -> list[GradeRecord]:
    session, referer = create_jw_session(username, password)
    request_headers = {
        "User-Agent": CAS_HEADERS["User-Agent"],
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Origin": JW_BASE_URL,
        "Referer": referer,
        "X-Requested-With": "XMLHttpRequest",
    }
    response = session.post(
        JW_GRADE_ENDPOINT,
        headers=request_headers,
        data={
            "querySetting": json.dumps(
                _query_setting(term_code), ensure_ascii=False, separators=(",", ":")
            ),
            "*order": "-XNXQDM,-KCH,-KXH",
        },
        timeout=30,
    )
    response.raise_for_status()
    payload = _json_response(response, "成绩查询接口")
    if str(payload.get("code", "0")) != "0":
        raise RuntimeError(payload.get("msg") or payload.get("message") or "成绩查询接口返回失败")
    rows = payload.get("datas", {}).get("xscjcx", {}).get("rows", [])
    if not isinstance(rows, list):
        raise RuntimeError("成绩查询接口未返回成绩列表。")
    return [normalize_grade(row) for row in rows if isinstance(row, dict)]
