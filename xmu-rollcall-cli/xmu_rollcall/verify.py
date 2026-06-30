import math
import time
import uuid

import requests

from .utils import base_url, headers, retry_request


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


def send_code(in_session, rollcall_id):
    code_url = f"{base_url}/api/rollcall/{rollcall_id}/student_rollcalls"
    answer_url = f"{base_url}/api/rollcall/{rollcall_id}/answer_number_rollcall"
    print("Trying number code from API...")
    started_at = time.time()
    request_headers = in_session.headers
    try:
        code_response = retry_request(
            lambda: in_session.get(code_url, headers=request_headers, timeout=15),
            max_attempts=3,
            delay=2,
            label="get_number_code",
        )
        if code_response.status_code != 200:
            print(f"Failed to get number code. Status: {code_response.status_code}\nTime: {time.time() - started_at:.2f} s.")
            return False
        code_data = code_response.json()
    except requests.RequestException as exc:
        print(f"Failed to request number code API: {exc}\nTime: {time.time() - started_at:.2f} s.")
        return False
    except ValueError as exc:
        print(f"Failed to parse number code API response: {exc}\nTime: {time.time() - started_at:.2f} s.")
        return False

    number_code = find_number_code(code_data)
    if not number_code:
        print(f"Failed to get number code. 'number_code' not found in API response.\nTime: {time.time() - started_at:.2f} s.")
        return False

    payload = {"deviceId": str(uuid.uuid4()), "numberCode": number_code}
    try:
        response = retry_request(
            lambda: in_session.put(answer_url, json=payload, headers=request_headers, timeout=15),
            max_attempts=3,
            delay=2,
            label="answer_number",
        )
        if response.status_code == 200:
            print("Number code rollcall answered successfully.\nNumber code:", number_code)
            time.sleep(5)
            print(f"Time: {time.time() - started_at:.2f} s.")
            return True
        print(f"Failed to submit number code. Status: {response.status_code}\nTime: {time.time() - started_at:.2f} s.")
        return False
    except requests.RequestException as exc:
        print(f"Failed to submit number code: {exc}\nTime: {time.time() - started_at:.2f} s.")
        return False


def send_radar(in_session, rollcall_id):
    url = f"{base_url}/api/rollcall/{rollcall_id}/answer"
    lat_1, lat_2 = 24.3, 24.6
    lon_1, lon_2 = 118.0, 118.2

    def payload(lat, lon):
        return {
            "accuracy": 35,
            "altitude": 0,
            "altitudeAccuracy": None,
            "deviceId": str(uuid.uuid4()),
            "heading": None,
            "latitude": lat,
            "longitude": lon,
            "speed": None,
        }

    res_1 = retry_request(
        lambda: in_session.put(url, json=payload(lat_1, lon_1), headers=headers, timeout=15),
        max_attempts=3,
        delay=2,
        label="radar_1",
    )
    data_1 = res_1.json()
    if res_1.status_code == 200:
        return True

    res_2 = retry_request(
        lambda: in_session.put(url, json=payload(lat_2, lon_2), headers=headers, timeout=15),
        max_attempts=3,
        delay=2,
        label="radar_2",
    )
    data_2 = res_2.json()
    if res_2.status_code == 200:
        return True

    distance_1 = data_1.get("distance")
    distance_2 = data_2.get("distance")

    def latlon_to_xy(lat, lon, lat0, lon0):
        radius = 6371000
        x = math.radians(lon - lon0) * radius * math.cos(math.radians(lat0))
        y = math.radians(lat - lat0) * radius
        return x, y

    def xy_to_latlon(x, y, lat0, lon0):
        radius = 6371000
        lat = lat0 + math.degrees(y / radius)
        lon = lon0 + math.degrees(x / (radius * math.cos(math.radians(lat0))))
        return lat, lon

    def circle_intersections(x1, y1, d1, x2, y2, d2):
        distance = math.hypot(x2 - x1, y2 - y1)
        if distance > d1 + d2 or distance < abs(d1 - d2):
            return None
        along = (d1 ** 2 - d2 ** 2 + distance ** 2) / (2 * distance)
        height = math.sqrt(d1 ** 2 - along ** 2)
        midpoint_x = x1 + along * (x2 - x1) / distance
        midpoint_y = y1 + along * (y2 - y1) / distance
        offset_x = -(y2 - y1) * height / distance
        offset_y = (x2 - x1) * height / distance
        return (
            (midpoint_x + offset_x, midpoint_y + offset_y),
            (midpoint_x - offset_x, midpoint_y - offset_y),
        )

    def solve_two_points(lat1, lon1, lat2, lon2, d1, d2):
        lat0 = (lat1 + lat2) / 2
        lon0 = (lon1 + lon2) / 2
        x1, y1 = latlon_to_xy(lat1, lon1, lat0, lon0)
        x2, y2 = latlon_to_xy(lat2, lon2, lat0, lon0)
        solutions = circle_intersections(x1, y1, d1, x2, y2, d2)
        if solutions is None:
            return None
        point_1 = xy_to_latlon(solutions[0][0], solutions[0][1], lat0, lon0)
        point_2 = xy_to_latlon(solutions[1][0], solutions[1][1], lat0, lon0)
        return point_1, point_2

    resolutions = solve_two_points(lat_1, lon_1, lat_2, lon_2, distance_1, distance_2)
    if not resolutions:
        return False
    (sol_x_1, sol_y_1), (sol_x_2, sol_y_2) = resolutions
    res_3 = in_session.put(url, json=payload(sol_x_1, sol_y_1), headers=headers, timeout=15)
    if res_3.status_code == 200:
        return True
    print(res_3.json())
    res_4 = in_session.put(url, json=payload(sol_x_2, sol_y_2), headers=headers, timeout=15)
    return res_4.status_code == 200
