"""Local account and cookie persistence for the desktop dashboard."""

from __future__ import annotations

import json
import os
from pathlib import Path


def get_config_dir() -> Path:
    """Return the writable config directory used by the desktop app."""
    if env_path := os.environ.get("XMU_ROLLCALL_CONFIG_DIR"):
        return Path(env_path)

    try:
        home_config_dir = Path.home() / ".xmu_rollcall"
        home_config_dir.mkdir(parents=True, exist_ok=True)
        test_file = home_config_dir / ".test_write"
        test_file.touch()
        test_file.unlink()
        return home_config_dir
    except (OSError, PermissionError, RuntimeError):
        return Path.cwd() / ".xmu_rollcall"


CONFIG_DIR = get_config_dir()
CONFIG_FILE = CONFIG_DIR / "config.json"

DEFAULT_ROLLCALL_SETTINGS = {
    "number_delay_min": 10,
    "number_delay_max": 30,
    "radar_delay_min": 0,
    "radar_delay_max": 0,
    "manual_confirm": False,
    "wait_before_answer_mode": "none",
    "wait_before_answer_count_min": 0,
    "wait_before_answer_count_max": 0,
}

DEFAULT_CONFIG = {
    "accounts": [],
    "current_account_id": None,
}


def normalize_rollcall_settings(settings: dict | None) -> dict:
    merged = DEFAULT_ROLLCALL_SETTINGS.copy()
    merged.update(settings or {})

    for key in (
        "number_delay_min",
        "number_delay_max",
        "radar_delay_min",
        "radar_delay_max",
        "wait_before_answer_count_min",
        "wait_before_answer_count_max",
    ):
        try:
            merged[key] = max(0, int(merged.get(key, DEFAULT_ROLLCALL_SETTINGS[key])))
        except (TypeError, ValueError):
            merged[key] = DEFAULT_ROLLCALL_SETTINGS[key]

    if merged["number_delay_max"] < merged["number_delay_min"]:
        merged["number_delay_max"] = merged["number_delay_min"]
    if merged["radar_delay_max"] < merged["radar_delay_min"]:
        merged["radar_delay_max"] = merged["radar_delay_min"]
    if merged["wait_before_answer_count_max"] < merged["wait_before_answer_count_min"]:
        merged["wait_before_answer_count_max"] = merged["wait_before_answer_count_min"]

    if merged.get("wait_before_answer_mode") not in ("none", "fixed", "random"):
        merged["wait_before_answer_mode"] = "none"
    merged["manual_confirm"] = bool(merged.get("manual_confirm", False))
    return merged


def ensure_config_dir() -> None:
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    except (OSError, PermissionError) as exc:
        raise RuntimeError(f"无法创建配置目录 {CONFIG_DIR}: {exc}") from exc


def load_config() -> dict:
    ensure_config_dir()
    if not CONFIG_FILE.exists():
        return DEFAULT_CONFIG.copy()

    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as file:
            config = json.load(file)
    except Exception as exc:
        raise RuntimeError(f"读取配置失败：{CONFIG_FILE}: {exc}") from exc

    if not isinstance(config, dict):
        return DEFAULT_CONFIG.copy()

    if "accounts" not in config and config.get("username"):
        config = {
            "accounts": [
                {
                    "id": 1,
                    "name": "",
                    "username": config.get("username", ""),
                    "password": config.get("password", ""),
                    "rollcall_settings": DEFAULT_ROLLCALL_SETTINGS.copy(),
                }
            ],
            "current_account_id": 1,
        }

    config.setdefault("accounts", [])
    config.setdefault("current_account_id", None)
    for account in config["accounts"]:
        if isinstance(account, dict):
            account["rollcall_settings"] = normalize_rollcall_settings(account.get("rollcall_settings"))
    return config


def save_config(config: dict) -> None:
    ensure_config_dir()
    with open(CONFIG_FILE, "w", encoding="utf-8") as file:
        json.dump(config, file, indent=2, ensure_ascii=False)


def get_next_account_id(config: dict) -> int:
    accounts = config.get("accounts", [])
    if not accounts:
        return 1
    return max(int(account.get("id", 0)) for account in accounts) + 1


def add_account(config: dict, username: str, password: str, name: str) -> int:
    account_id = get_next_account_id(config)
    account = {
        "id": account_id,
        "name": name,
        "username": username,
        "password": password,
        "rollcall_settings": DEFAULT_ROLLCALL_SETTINGS.copy(),
    }
    config.setdefault("accounts", []).append(account)
    if config.get("current_account_id") is None:
        config["current_account_id"] = account_id
    return account_id


def get_account_by_id(config: dict, account_id: int | str | None) -> dict | None:
    for account in config.get("accounts", []):
        if str(account.get("id")) == str(account_id):
            return account
    return None


def get_current_account(config: dict) -> dict | None:
    return get_account_by_id(config, config.get("current_account_id"))


def set_current_account(config: dict, account_id: int | str) -> None:
    config["current_account_id"] = account_id


def get_all_accounts(config: dict) -> list[dict]:
    return config.get("accounts", [])


def get_rollcall_settings(account: dict) -> dict:
    return normalize_rollcall_settings(account.get("rollcall_settings") or {})


def set_rollcall_settings(account: dict, settings: dict | None) -> None:
    account["rollcall_settings"] = normalize_rollcall_settings(settings)


def get_cookies_path(account_id: int | str | None = None) -> str:
    ensure_config_dir()
    if account_id is None:
        account_id = load_config().get("current_account_id", 1)
    return str(CONFIG_DIR / f"{account_id}.json")
