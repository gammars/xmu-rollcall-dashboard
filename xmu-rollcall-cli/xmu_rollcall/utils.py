import json
import os
import time as _time

import requests


def retry_request(fn, max_attempts=3, delay=2, backoff=2, label="request"):
    """Run a request callable with exponential-backoff retries."""
    last_exc = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            if attempt < max_attempts:
                _time.sleep(delay)
                delay *= backoff
    raise last_exc


def supports_interactive_terminal() -> bool:
    try:
        return bool(os.environ.get("TERM") and os.isatty(1) and os.isatty(2))
    except Exception:
        return False


base_url = "https://lnt.xmu.edu.cn"

headers = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": "https://ids.xmu.edu.cn/authserver/login",
}


def clear_screen():
    if not supports_interactive_terminal():
        return
    if os.name == "nt":
        os.system("cls")
    else:
        os.system("clear")


def save_session(sess: requests.Session, path: str):
    try:
        cookie_dict = requests.utils.dict_from_cookiejar(sess.cookies)
        with open(path, "w", encoding="utf-8") as file:
            json.dump(cookie_dict, file)
    except Exception:
        return


def load_session(sess: requests.Session, path: str):
    try:
        with open(path, "r", encoding="utf-8") as file:
            cookie_dict = json.load(file)
        sess.cookies = requests.utils.cookiejar_from_dict(cookie_dict)
        return True
    except Exception:
        return False


def verify_session(sess: requests.Session) -> dict:
    """Verify that a TronClass session is still valid."""
    try:
        response = sess.get(f"{base_url}/api/profile", headers=headers, timeout=15)
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, dict) and "name" in data:
                return data
    except Exception:
        pass
    return {}
