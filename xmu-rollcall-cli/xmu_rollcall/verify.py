import uuid

import requests

from .utils import base_url, headers


def find_number_code(data, depth=0, max_depth=10):
    """Extract ``number_code`` from nested TronClass API responses."""
    if depth > max_depth:
        return None
    if isinstance(data, dict):
        number_code = data.get("number_code")
        if number_code is not None:
            return str(number_code)
        for value in data.values():
            nested_code = find_number_code(value, depth + 1, max_depth)
            if nested_code:
                return nested_code
    elif isinstance(data, list):
        for item in data:
            nested_code = find_number_code(item, depth + 1, max_depth)
            if nested_code:
                return nested_code
    return None


def send_code(in_session, rollcall_id, number_code=None):
    """Submit an already resolved number code with a single PUT request.

    When called by legacy CLI code without a code, resolve it through the new
    timetable_rollcalls flow.  The dashboard always supplies the snapshot code.
    """
    answer_url = f"{base_url}/api/rollcall/{rollcall_id}/answer_number_rollcall"
    if number_code is None:
        from .desktop_qt.core import fetch_rollcall_snapshot
        number_code = fetch_rollcall_snapshot(in_session, str(rollcall_id)).number_code
    if not number_code:
        return False
    payload = {"deviceId": str(uuid.uuid4()), "numberCode": str(number_code)}
    try:
        response = in_session.put(answer_url, json=payload, headers=in_session.headers or headers, timeout=15)
        return response.status_code == 200
    except requests.RequestException:
        return False


def send_radar(in_session, rollcall_id, latitude, longitude, accuracy=35):
    """Submit one configured radar location; intentionally never retries."""
    url = f"{base_url}/api/rollcall/{rollcall_id}/answer"
    payload = {
        "accuracy": float(accuracy),
        "altitude": 0,
        "altitudeAccuracy": None,
        "deviceId": str(uuid.uuid4()),
        "heading": None,
        "latitude": float(latitude),
        "longitude": float(longitude),
        "speed": None,
    }
    try:
        response = in_session.put(url, json=payload, headers=in_session.headers or headers, timeout=15)
        return response.status_code == 200
    except requests.RequestException:
        return False
