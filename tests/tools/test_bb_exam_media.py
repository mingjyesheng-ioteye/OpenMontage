"""bb_exam delivery: resolve the course, then upload the module media."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.publishers.bb_exam_media import BbExamMedia


class _Response:
    def __init__(self, status: int, payload: dict | None = None, text: str = "") -> None:
        self.status_code = status
        self._payload = payload or {}
        self.text = text
        self.content = json.dumps(payload).encode() if payload is not None else b""

    def json(self) -> dict:
        return self._payload


def test_publish_requires_a_readable_file(tmp_path: Path) -> None:
    result = BbExamMedia().execute({
        "file_path": str(tmp_path / "missing.mp4"),
        "module_key": "m01",
        "slug": "course",
    })
    assert result.success is False
    assert "not found" in (result.error or "").lower()


def test_publish_requires_credentials(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    media = tmp_path / "lesson.mp4"
    media.write_bytes(b"video")
    for key in ("BB_EXAM_TOKEN", "BB_EXAM_EMAIL", "BB_EXAM_PASSWORD"):
        monkeypatch.delenv(key, raising=False)
    result = BbExamMedia().execute({
        "file_path": str(media),
        "module_key": "m01",
        "slug": "course",
    })
    assert result.success is False
    assert "credentials" in (result.error or "").lower()


def test_publish_logs_in_resolves_slug_and_uploads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    media = tmp_path / "lesson.mp4"
    media.write_bytes(b"video-bytes")
    monkeypatch.setenv("BB_EXAM_EMAIL", "service@example.com")
    monkeypatch.setenv("BB_EXAM_PASSWORD", "secret")
    monkeypatch.delenv("BB_EXAM_TOKEN", raising=False)

    calls: dict[str, object] = {}

    def fake_post(url, **kwargs):
        calls.setdefault("posts", []).append((url, kwargs))
        if url.endswith("/api/auth/login"):
            return _Response(200, {"token": "token-123"})
        return _Response(201, {"media": {"url": "https://lms/media/lesson.mp4"}})

    def fake_get(url, **kwargs):
        calls["get"] = (url, kwargs)
        return _Response(200, {
            "course": {"id": "course-1", "versionId": "version-9", "slug": "course"}
        })

    import requests

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(requests, "get", fake_get)

    result = BbExamMedia().execute({
        "file_path": str(media),
        "module_key": "functions-and-causes-of-behavior",
        "slug": "course",
        "base_url": "http://127.0.0.1:3004",
    })

    assert result.success is True, result.error
    assert result.data["url"] == "https://lms/media/lesson.mp4"
    assert result.data["course_id"] == "course-1"
    assert result.data["version_id"] == "version-9"

    upload_url, upload_kwargs = calls["posts"][-1]
    assert upload_url.endswith("/api/courses/course-1/versions/version-9/media")
    assert upload_kwargs["data"]["moduleKey"] == "functions-and-causes-of-behavior"
    assert upload_kwargs["data"]["kind"] == "lesson_video"
    assert upload_kwargs["headers"]["Authorization"] == "Bearer token-123"
    filename, handle, mime = upload_kwargs["files"]["file"]
    assert filename == "lesson.mp4"
    assert mime == "video/mp4"
