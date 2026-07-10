from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "xmu-rollcall-cli"))

from xmu_rollcall.courseware import (  # noqa: E402
    CoursewareItem,
    available_path,
    download_courseware,
    fetch_courses,
    fetch_courseware,
    format_size,
    sanitize_filename,
)


def make_item(**overrides) -> CoursewareItem:
    values = {
        "course_id": "1",
        "activity_id": "2",
        "activity_title": "Lecture",
        "activity_type": "material",
        "module_name": "PPT",
        "syllabus_name": "",
        "upload_id": "3",
        "reference_id": "4",
        "filename": "lecture.pdf",
        "size": 2048,
        "media_type": "document",
        "published_at": "2026-01-01T00:00:00Z",
        "upload_status": "ready",
        "allow_download": True,
    }
    values.update(overrides)
    return CoursewareItem(**values)


class FakeResponse:
    status_code = 200
    headers = {"Content-Type": "application/pdf"}

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size):
        yield b"part-one"
        yield b"part-two"

    def close(self):
        return None


class FakeSignedUrlResponse:
    status_code = 200
    headers = {"Content-Type": "application/json"}

    def raise_for_status(self):
        return None

    def json(self):
        return {"url": "https://storage.example.test/signed-file"}


class FakeSession:
    def __init__(self):
        self.urls = []

    def get(self, url, **kwargs):
        self.urls.append(url)
        if url.endswith("/api/uploads/reference/4/url"):
            return FakeSignedUrlResponse()
        if url == "https://storage.example.test/signed-file":
            return FakeResponse()
        raise AssertionError(f"Unexpected request: {url}")


class CoursewareTests(unittest.TestCase):
    def test_download_status_respects_platform_permission(self):
        self.assertEqual(make_item().download_status, "可下载")
        self.assertEqual(make_item(allow_download=False).download_status, "可下载（平台版权保护）")
        self.assertEqual(make_item(upload_id="").download_status, "无附件")

    def test_sanitize_filename_for_windows(self):
        self.assertEqual(sanitize_filename('a<b>:c?.pdf'), "a_b__c_.pdf")
        self.assertEqual(sanitize_filename("CON.txt"), "_CON.txt")

    def test_available_path_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            (directory / "lecture.pdf").write_bytes(b"existing")
            self.assertEqual(available_path(directory, "lecture.pdf").name, "lecture (2).pdf")

    def test_download_streams_to_final_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            session = FakeSession()
            target = download_courseware(session, make_item(), temp_dir)
            self.assertEqual(target.read_bytes(), b"part-onepart-two")
            self.assertFalse(target.with_name(target.name + ".part").exists())
            self.assertTrue(session.urls[0].endswith("/api/uploads/reference/4/url"))
            self.assertEqual(session.urls[1], "https://storage.example.test/signed-file")

    def test_download_allows_copyright_protected_item(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            session = FakeSession()
            target = download_courseware(session, make_item(allow_download=False), temp_dir)
            self.assertEqual(target.read_bytes(), b"part-onepart-two")
            self.assertTrue(session.urls[0].endswith("/api/uploads/reference/4/url"))

    def test_format_size(self):
        self.assertEqual(format_size(2048), "2.0 KB")

    def test_fetch_courses_keeps_platform_order_and_filter_metadata(self):
        class JsonResponse:
            status_code = 200

            def raise_for_status(self):
                return None

            def json(self):
                return {"courses": [
                    {
                        "id": "c2",
                        "name": "Second Course",
                        "academic_year": {"name": "2025-2026"},
                        "semester": {"code": "2025-2026-2"},
                    },
                    {
                        "id": "c1",
                        "name": "First Course",
                        "academic_year": {"name": "2025-2026"},
                        "semester": {"code": "2025-2026-1"},
                    },
                ]}

        class CourseListSession:
            def get(self, url, **kwargs):
                return JsonResponse()

        courses, _source = fetch_courses(CourseListSession())
        self.assertEqual([course.course_id for course in courses], ["c2", "c1"])
        self.assertEqual(courses[0].term, "2025-2026")
        self.assertEqual(courses[0].semester_code, "2025-2026-2")
        self.assertIn("2025-2026", courses[0].search_text)

    def test_homework_is_filtered_before_detail_request(self):
        class JsonResponse:
            status_code = 200

            def __init__(self, payload):
                self.payload = payload

            def raise_for_status(self):
                return None

            def json(self):
                return self.payload

        class TrackingSession:
            def __init__(self):
                self.urls = []

            def get(self, url, **kwargs):
                self.urls.append(url)
                if url.endswith("/courseware-activities"):
                    return JsonResponse([
                        {"id": 10, "type": "homework", "title": "Assignment"},
                        {"id": 11, "type": "material", "title": "Slides"},
                    ])
                if url.endswith("/modules"):
                    return JsonResponse({"modules": []})
                if url.endswith("/activities/11"):
                    return JsonResponse({
                        "id": 11,
                        "type": "material",
                        "title": "Slides",
                        "uploads": [],
                    })
                raise AssertionError(f"Unexpected request: {url}")

        session = TrackingSession()
        items = fetch_courseware(session, "1")
        self.assertEqual([item.activity_id for item in items], ["11"])
        self.assertFalse(any(url.endswith("/activities/10") for url in session.urls))

    def test_courseware_details_follow_module_order(self):
        class JsonResponse:
            status_code = 200

            def __init__(self, payload):
                self.payload = payload

            def raise_for_status(self):
                return None

            def json(self):
                return self.payload

        class MultiActivitySession:
            def __init__(self):
                self.urls = []

            def get(self, url, **kwargs):
                self.urls.append(url)
                if url.endswith("/courseware-activities"):
                    return JsonResponse([
                        {"id": 22, "type": "material", "title": "B", "module_id": "m2", "syllabus_id": 0},
                        {"id": 21, "type": "material", "title": "A", "module_id": "m1", "syllabus_id": 0},
                        {"id": 23, "type": "homework", "title": "Homework"},
                    ])
                if url.endswith("/modules"):
                    return JsonResponse({"modules": [
                        {"id": "m1", "name": "第一章", "syllabuses": []},
                        {"id": "m2", "name": "第二章", "syllabuses": []},
                    ]})
                if url.endswith("/activities/21"):
                    return JsonResponse({
                        "id": 21,
                        "type": "material",
                        "title": "A",
                        "module_id": "m1",
                        "syllabus_id": 0,
                        "uploads": [{
                            "id": "u21",
                            "reference_id": "r21",
                            "name": "a.pdf",
                            "size": 10,
                            "status": "ready",
                            "allow_download": True,
                        }],
                    })
                if url.endswith("/activities/22"):
                    return JsonResponse({
                        "id": 22,
                        "type": "material",
                        "title": "B",
                        "module_id": "m2",
                        "syllabus_id": 0,
                        "uploads": [{
                            "id": "u22",
                            "reference_id": "r22",
                            "name": "b.pdf",
                            "size": 20,
                            "status": "ready",
                            "allow_download": False,
                        }],
                    })
                raise AssertionError(f"Unexpected request: {url}")

        session = MultiActivitySession()
        items = fetch_courseware(session, "1")
        self.assertEqual([item.activity_id for item in items], ["21", "22"])
        self.assertEqual(items[1].download_status, "可下载（平台版权保护）")
        self.assertFalse(any(url.endswith("/activities/23") for url in session.urls))


if __name__ == "__main__":
    unittest.main()
