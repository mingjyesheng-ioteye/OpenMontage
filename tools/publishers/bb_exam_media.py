"""Deliver lesson media into a bb_exam course version.

The BrainBook Video publish stage needs a way to put a finished render (video,
captions, thumbnail) into the LMS module the lesson was authored for. bb_exam
exposes that through its version-media endpoint; this tool performs the same
upload natively so the pipeline does not depend on another repository's helper
script.

Resolution order:

1. ``--course`` + ``--version`` (ids already known), or
2. the published course slug via bb_exam's public API.

Credentials come from the tool inputs or ``BB_EXAM_TOKEN`` /
``BB_EXAM_EMAIL`` + ``BB_EXAM_PASSWORD``. Publishing is an explicit, approved
action — this tool never runs from compose.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from tools.base_tool import (
    BaseTool,
    Determinism,
    ExecutionMode,
    ResourceProfile,
    RetryPolicy,
    ToolResult,
    ToolRuntime,
    ToolStability,
    ToolStatus,
    ToolTier,
)

_MIME = {
    "lesson_video": "video/mp4",
    "captions": "text/vtt",
    "thumbnail": "image/png",
}
_DEFAULT_BASE = "http://127.0.0.1:3004"


class BbExamMedia(BaseTool):
    name = "bb_exam_media"
    version = "1.0.0"
    tier = ToolTier.PUBLISH
    capability = "publish"
    provider = "bb_exam"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.DETERMINISTIC
    runtime = ToolRuntime.LOCAL

    dependencies = []
    install_instructions = (
        "Start bb_exam (default http://127.0.0.1:3004) and provide a service "
        "account: BB_EXAM_TOKEN, or BB_EXAM_EMAIL + BB_EXAM_PASSWORD."
    )
    agent_skills = []

    capabilities = ["upload_lesson_media", "resolve_course_version"]
    supports = {
        "uploads": True,
        "requires_network": True,
        "video": True,
        "captions": True,
        "thumbnail": True,
    }
    best_for = [
        "delivering a finished lesson into the bb_exam module it was authored for",
        "uploading rendered captions or a thumbnail alongside the video",
    ]
    not_good_for = [
        "publishing a course or changing learner visibility (do that in bb_exam)",
        "running without credentials or a reachable LMS",
    ]

    input_schema = {
        "type": "object",
        "required": ["file_path", "module_key"],
        "properties": {
            "file_path": {"type": "string", "description": "Rendered file to upload"},
            "module_key": {
                "type": "string",
                "description": "bb_exam module key (e.g. functions-and-causes-of-behavior)",
            },
            "kind": {
                "type": "string",
                "enum": list(_MIME),
                "description": "Media kind (default lesson_video)",
            },
            "slug": {
                "type": "string",
                "description": "Published course slug (resolves course/version)",
            },
            "course_id": {"type": "string"},
            "version_id": {"type": "string"},
            "name": {"type": "string", "description": "Filename sent to bb_exam"},
            "base_url": {"type": "string", "description": "bb_exam base URL"},
            "token": {"type": "string", "description": "Privileged bearer token"},
            "email": {"type": "string", "description": "Service account email"},
            "password": {"type": "string", "description": "Service account password"},
            "timeout_seconds": {"type": "integer"},
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=1, ram_mb=256, vram_mb=0, disk_mb=50, network_required=True,
    )
    retry_policy = RetryPolicy(max_retries=1, retryable_errors=["timeout"])
    idempotency_key_fields = ["file_path", "module_key", "kind", "slug"]
    side_effects = ["uploads media into a bb_exam course version"]
    user_visible_verification = [
        "Open the module in the bb_exam learner player and confirm the media plays",
    ]

    def get_status(self) -> ToolStatus:
        try:
            import requests  # noqa: F401
        except ImportError:
            return ToolStatus.UNAVAILABLE
        return ToolStatus.AVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0

    # ------------------------------------------------------------------

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        try:
            import requests
        except ImportError:
            return ToolResult(
                success=False, error="requests is not installed. " + self.install_instructions
            )

        file_path = Path(str(inputs["file_path"])).expanduser()
        if not file_path.is_absolute():
            file_path = (Path.cwd() / file_path).resolve()
        if not file_path.is_file():
            return ToolResult(success=False, error=f"File not found: {file_path}")

        kind = str(inputs.get("kind") or "lesson_video")
        if kind not in _MIME:
            return ToolResult(
                success=False, error=f"Unsupported kind {kind!r}; choose {sorted(_MIME)}."
            )

        base = str(
            inputs.get("base_url")
            or os.environ.get("BB_EXAM_BASE_URL")
            or _DEFAULT_BASE
        ).rstrip("/")
        timeout = int(inputs.get("timeout_seconds") or 180)

        token = str(inputs.get("token") or os.environ.get("BB_EXAM_TOKEN") or "").strip()
        email = str(inputs.get("email") or os.environ.get("BB_EXAM_EMAIL") or "").strip()
        password = str(
            inputs.get("password") or os.environ.get("BB_EXAM_PASSWORD") or ""
        )
        if not token:
            if not (email and password):
                return ToolResult(
                    success=False,
                    error="bb_exam credentials missing: set BB_EXAM_TOKEN or "
                          "BB_EXAM_EMAIL + BB_EXAM_PASSWORD.",
                )
            try:
                login = requests.post(
                    f"{base}/api/auth/login",
                    json={"email": email, "password": password},
                    timeout=timeout,
                )
            except requests.RequestException as exc:
                return ToolResult(success=False, error=f"bb_exam login failed: {exc}")
            payload = login.json() if login.content else {}
            token = str((payload or {}).get("token") or "").strip()
            if login.status_code != 200 or not token:
                return ToolResult(
                    success=False,
                    error=f"bb_exam login returned HTTP {login.status_code}.",
                )

        course_id = str(inputs.get("course_id") or "")
        version_id = str(inputs.get("version_id") or "")
        slug = str(inputs.get("slug") or "")
        if not (course_id and version_id):
            if not slug:
                return ToolResult(
                    success=False,
                    error="Provide `slug`, or both `course_id` and `version_id`.",
                )
            try:
                lookup = requests.get(
                    f"{base}/api/courses/public/{slug}",
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=timeout,
                )
            except requests.RequestException as exc:
                return ToolResult(success=False, error=f"bb_exam course lookup failed: {exc}")
            if lookup.status_code != 200:
                return ToolResult(
                    success=False,
                    error=f"bb_exam course lookup returned HTTP {lookup.status_code} for {slug!r}.",
                )
            course = (lookup.json() or {}).get("course") or {}
            course_id = course_id or str(course.get("id") or "")
            version_id = version_id or str(course.get("versionId") or "")
            if not (course_id and version_id):
                return ToolResult(
                    success=False,
                    error=f"Could not resolve a published version for course {slug!r}.",
                )

        module_key = str(inputs["module_key"])
        upload_url = (
            f"{base}/api/courses/{course_id}/versions/{version_id}/media"
        )
        try:
            with file_path.open("rb") as handle:
                response = requests.post(
                    upload_url,
                    headers={"Authorization": f"Bearer {token}"},
                    data={"moduleKey": module_key, "kind": kind},
                    files={"file": (inputs.get("name") or file_path.name, handle, _MIME[kind])},
                    timeout=max(timeout, 600),
                )
        except requests.RequestException as exc:
            return ToolResult(success=False, error=f"bb_exam upload failed: {exc}")

        if response.status_code not in (200, 201):
            detail = (response.text or "")[:400]
            return ToolResult(
                success=False,
                error=f"bb_exam upload returned HTTP {response.status_code}: {detail}",
            )
        media = (response.json() or {}).get("media") or {}
        return ToolResult(
            success=True,
            data={
                "provider": self.provider,
                "kind": kind,
                "module_key": module_key,
                "course_id": course_id,
                "version_id": version_id,
                "url": media.get("url"),
                "file": str(file_path),
                "bytes": file_path.stat().st_size,
                "base_url": base,
            },
            artifacts=[str(file_path)],
            model=None,
        )
