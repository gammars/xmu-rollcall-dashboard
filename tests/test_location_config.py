from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "xmu-rollcall-cli"))

from xmu_rollcall.config import delete_location_preset, normalize_location_presets, normalize_rollcall_settings


def test_location_validation_and_stable_id():
    presets = normalize_location_presets([
        {"id": "stable", "name": "教室", "latitude": 24, "longitude": 118, "accuracy": 35},
        {"name": "bad", "latitude": 91, "longitude": 118, "accuracy": 35},
    ])
    assert presets[0] == {
        "id": "stable", "name": "教室", "latitude": 24.0, "longitude": 118.0,
        "accuracy": 35.0, "built_in": False,
    }
    assert {item["name"] for item in presets[1:]} == {"学武楼", "西部片区"}


def test_delete_preset_removes_all_account_bindings():
    config = {
        "location_presets": [{"id": "p", "name": "教室", "latitude": 24, "longitude": 118, "accuracy": 35}],
        "accounts": [{"rollcall_settings": {"course_location_bindings": {
            "9": {"preset_id": "p", "course_title": "软件工程"},
            "10": {"preset_id": "other", "course_title": "测试"},
        }}}],
    }
    assert delete_location_preset(config, "p") == 1
    bindings = config["accounts"][0]["rollcall_settings"]["course_location_bindings"]
    assert list(bindings) == ["10"]


def test_old_settings_get_non_destructive_binding_default():
    settings = normalize_rollcall_settings({"attendance_threshold_percent": 12})
    assert settings["attendance_threshold_percent"] == 12
    assert settings["course_location_bindings"] == {}
