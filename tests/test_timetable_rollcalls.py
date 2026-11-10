from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "xmu-rollcall-cli"))

from xmu_rollcall.desktop_qt.core import (
    CourseRollcallRecord,
    enrich_course_rollcall_records,
    enrich_course_record_from_detail,
    fetch_rollcall_snapshot,
    rollcall_utc_date,
    snapshot_from_timetable_row,
    snapshot_self_status,
    timetable_rollcalls_from_payload,
)
from xmu_rollcall.verify import send_radar


class Response:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


def row(rollcall_id="42", code="0026", status="started", students=None, when="2026-10-18T16:30:00Z"):
    return {
        "id": int(rollcall_id),
        "course": {"id": 9, "name": "软件工程"},
        "rollcall_time": when,
        "end_time": "2099-10-18T17:00:00Z",
        "status": status,
        "is_number": True,
        "number_code": code,
        "student_rollcalls": students if students is not None else [
            {"student_id": 1, "status": "on_call"},
            {"student_id": 2, "status": "absent"},
        ],
    }


def test_utc_date_handles_beijing_after_midnight():
    assert rollcall_utc_date("2026-10-19T00:30:00+08:00") == "2026-10-18"
    assert rollcall_utc_date("2026-10-19T08:30:00+08:00") == "2026-10-19"


def test_snapshot_preserves_leading_zero_and_counts_statuses():
    snapshot = snapshot_from_timetable_row(row())
    assert snapshot.number_code == "0026"
    assert (snapshot.signed_count, snapshot.total_count) == (1, 2)
    assert snapshot_self_status(snapshot, "1") == "已签到"
    assert snapshot_self_status(snapshot, "2") == "未签到"


def test_fetch_snapshot_uses_exact_id_not_other_same_day():
    session = Mock()
    session.get.side_effect = [
        Response({"course_id": 9, "rollcall_time": "2026-10-19T00:30:00+08:00"}),
        Response({"rollcalls": [row("41", "9999"), row("42", "0026")]}),
    ]
    snapshot = fetch_rollcall_snapshot(session, "42")
    assert snapshot.number_code == "0026"
    assert session.get.call_args_list[1].kwargs["params"] == {
        "course_ids": "9", "rollcall_date": "2026-10-18"
    }


@pytest.mark.parametrize("payload", [None, [], {}, {"rollcalls": None}])
def test_invalid_timetable_json_is_rejected(payload):
    with pytest.raises(RuntimeError):
        timetable_rollcalls_from_payload(payload)


def test_empty_code_and_finished_snapshot():
    snapshot = snapshot_from_timetable_row(row(code=None, status="finished", students=[]))
    assert snapshot.number_code == ""
    assert snapshot.total_count == 0
    assert snapshot.ended


def test_partial_enrichment_failure_keeps_basic_record():
    record = CourseRollcallRecord("9", "软件工程", "42", "2026-10-19 00:30:00", "数字签到", "未知", "-")
    session = Mock()
    session.get.return_value = Response(ValueError("bad json"))
    enrich_course_rollcall_records(session, [record], "1")
    assert record.rollcall_id == "42"
    assert record.number_code == "-"
    assert record.enrichment_error


def test_another_record_falls_back_to_detail_for_attendance_only():
    record = CourseRollcallRecord("9", "软件工程", "451668", "2026-10-13 15:15:00", "签到", "未知", "-")
    session = Mock()
    session.get.return_value = Response({
        "status": "finished", "type": "another", "is_number": False,
        "end_time": "2026-10-13T07:16:00Z",
        "student_rollcalls": [
            {"student_id": 1, "status": "on_call"},
            {"student_id": 2, "status": "absent"},
        ],
    })
    assert enrich_course_record_from_detail(session, record, "1")
    assert record.attendance == "1/2"
    assert record.number_code == "-"
    assert record.signed_status == "已签到"
    assert not record.enrichment_error


def test_radar_put_is_never_retried():
    session = Mock()
    session.headers = {}
    session.put.side_effect = RuntimeError("network")
    with pytest.raises(RuntimeError):
        send_radar(session, "42", 24.0, 118.0, 35)
    assert session.put.call_count == 1

