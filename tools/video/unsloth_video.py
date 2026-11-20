"""Video generation through a local Unsloth Studio server.

Studio exposes an async job API (``POST /api/inference/video/generate`` plus a
progress route), so this tool submits, waits, and downloads the finished MP4.
The loaded family decides the real capabilities -- MiniMax H3, for example,
renders native stereo audio and accepts first/last frames and references.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from tools._unsloth.client import (
    UnslothAuthError,
    UnslothClient,
    UnslothError,
    snap_dimension,
    snap_frames,
    to_data_url,
)
from tools._unsloth.metadata import UNSLOTH_SETUP_OFFER
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
from tools.video._shared import probe_output

# See unsloth_image: short status cache so preflight does not re-scan the catalog.
_STATUS_TTL_SECONDS = 30.0
_MAX_REFERENCE_IMAGES = 9


class UnslothVideo(BaseTool):
    name = "unsloth_video"
    version = "0.1.0"
    tier = ToolTier.GENERATE
    capability = "video_generation"
    provider = "unsloth"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.SEEDED
    runtime = ToolRuntime.LOCAL_GPU

    dependencies = []
    setup_offer = UNSLOTH_SETUP_OFFER
    install_instructions = (
        "Run Unsloth Studio locally (default http://127.0.0.1:8888) and set "
        "UNSLOTH_SERVER_URL if it is not the default.\n"
        "Authenticate with UNSLOTH_API_KEY (Settings -> API keys) or "
        "UNSLOTH_USERNAME / UNSLOTH_PASSWORD. Studio rejects API calls until its "
        "seeded bootstrap password is changed once.\n"
        "Load or download a video model in Studio first (MiniMax H3 is the usual "
        "choice); generation auto-loads it when media auto-switch is on.\n"
        "Running a Studio instance dedicated to video? Set UNSLOTH_VIDEO_SERVER_URL "
        "instead -- it takes priority over UNSLOTH_SERVER_URL for this tool only."
    )
    agent_skills = ["ai-video-gen", "minimax-h3"]

    capabilities = ["text_to_video", "image_to_video"]
    supports = {
        "seed": True,
        "custom_size": True,
        "reference_image": True,
        "last_frame": True,
        "reference_images": True,
        "native_audio": True,
        "offline": True,
    }
    best_for = [
        "local video generation on the GPU Unsloth Studio already manages",
        "MiniMax H3 with native stereo audio, first/last frames, and references",
        "zero API cost clips without a second copy of the model stack",
    ]
    not_good_for = [
        "setups without a running Unsloth Studio server",
        "Studio instances still on the seeded bootstrap password",
        "concurrent video jobs (Studio runs one generation slot)",
    ]
    fallback = "comfyui_video"
    fallback_tools = ["comfyui_video", "wan_video", "hunyuan_video", "ltx_video_local"]

    input_schema = {
        "type": "object",
        "required": ["prompt"],
        "properties": {
            "prompt": {"type": "string", "description": "Text prompt for video generation"},
            "operation": {
                "type": "string",
                "enum": ["text_to_video", "image_to_video"],
                "default": "text_to_video",
            },
            "negative_prompt": {
                "type": "string",
                "description": "What to avoid (only for models that support guidance).",
            },
            "reference_image_path": {
                "type": "string",
                "description": "Local image used as the first frame (image_to_video).",
            },
            "reference_image_url": {
                "type": "string",
                "description": "URL of the first-frame image (image_to_video).",
            },
            "reference_image_paths": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": _MAX_REFERENCE_IMAGES,
                "description": (
                    "Reference images for subject/style conditioning (Studio exposes "
                    "them to the prompt as <Picture 1>, <Picture 2>, ...)."
                ),
            },
            "width": {
                "type": "integer",
                "minimum": 32,
                "maximum": 2048,
                "description": "Frame width. Omit to use the family's preset.",
            },
            "height": {
                "type": "integer",
                "minimum": 32,
                "maximum": 2048,
                "description": "Frame height. Omit to use the family's preset.",
            },
            "num_frames": {
                "type": "integer",
                "minimum": 1,
                "maximum": 1024,
                "description": (
                    "Frame count; snapped to the family's lattice (MiniMax H3: 17k+5)."
                ),
            },
            "duration": {
                "type": "integer",
                "minimum": 1,
                "maximum": 30,
                "description": "Clip length in seconds when num_frames is omitted.",
            },
            "fps": {"type": "integer", "minimum": 1, "maximum": 120},
            "steps": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "description": "Denoising steps. Omit for the family default.",
            },
            "guidance": {
                "type": "number",
                "minimum": 0,
                "maximum": 20,
                "description": "Guidance scale. Omit for the family default.",
            },
            "seed": {"type": "integer", "minimum": 0, "description": "Random if omitted"},
            "flow_shift": {
                "type": "number",
                "exclusiveMinimum": 0,
                "description": "Video sigma shift (MiniMax H3 ships 12.0).",
            },
            "audio_flow_shift": {
                "type": "number",
                "exclusiveMinimum": 0,
                "description": "Audio sigma shift (MiniMax H3 ships 3.0).",
            },
            "model": {
                "type": "string",
                "description": "Optional Studio model id to serve on (auto-switched).",
            },
            "generate_audio": {
                "type": "boolean",
                "default": True,
                "description": (
                    "Accepted for selector compatibility. Families with native audio "
                    "(MiniMax H3) always render their audio track."
                ),
            },
            "output_path": {"type": "string", "description": "Where to save the video"},
            "timeout_seconds": {
                "type": "integer",
                "description": "How long to wait for the clip (default 3600s).",
            },
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=2, ram_mb=32000, vram_mb=16000, disk_mb=2000, network_required=False,
    )
    retry_policy = RetryPolicy(max_retries=1, retryable_errors=["timeout"])
    idempotency_key_fields = ["prompt", "operation", "width", "height", "num_frames", "seed"]
    side_effects = ["writes video file to output_path"]
    user_visible_verification = [
        "Watch generated clip for motion coherence, audio sync, and artifacts"
    ]

    def __init__(self) -> None:
        self._client = UnslothClient(capability="video")
        self._status_cache: tuple[float, ToolStatus, str, dict[str, Any]] | None = None
        self._last_progress_log = 0.0

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def _probe(self) -> tuple[ToolStatus, str, dict[str, Any]]:
        now = time.monotonic()
        if self._status_cache and now - self._status_cache[0] < _STATUS_TTL_SECONDS:
            return self._status_cache[1], self._status_cache[2], self._status_cache[3]
        result = self._probe_uncached()
        self._status_cache = (now, *result)
        return result

    def _probe_uncached(self) -> tuple[ToolStatus, str, dict[str, Any]]:
        if not self._client.is_available():
            return ToolStatus.UNAVAILABLE, self._client.unavailable_reason(), {}
        if not UnslothClient.has_credentials():
            return (
                ToolStatus.DEGRADED,
                "Unsloth Studio is reachable but no credentials are configured: set "
                "UNSLOTH_API_KEY (or UNSLOTH_USERNAME / UNSLOTH_PASSWORD).",
                {},
            )
        try:
            status = self._client.video_status()
        except UnslothAuthError as exc:
            return ToolStatus.DEGRADED, str(exc), {}
        except UnslothError as exc:
            return ToolStatus.DEGRADED, str(exc), {}
        if status.get("loaded"):
            return ToolStatus.AVAILABLE, "", status
        try:
            catalog = self._client.models("video")
        except UnslothError as exc:
            return ToolStatus.DEGRADED, str(exc), status
        if catalog:
            return ToolStatus.AVAILABLE, "", status
        return (
            ToolStatus.DEGRADED,
            "Unsloth Studio has no video model loaded or downloaded. Load or download "
            "one (MiniMax H3) in the Studio UI.",
            status,
        )

    def operation_statuses(self) -> dict[str, str]:
        """Per-operation readiness for selector routing and preflight."""
        status, _, payload = self._probe()
        base = {
            ToolStatus.AVAILABLE: "available",
            ToolStatus.DEGRADED: "degraded",
            ToolStatus.UNAVAILABLE: "unavailable",
        }[status]
        i2v = base
        if base == "available" and payload.get("loaded") and payload.get(
            "supports_first_frame"
        ) is False:
            i2v = "degraded"
        return {"text_to_video": base, "image_to_video": i2v}

    def is_operation_available(self, operation: str) -> bool:
        if operation not in {"text_to_video", "image_to_video"}:
            return False
        return self.operation_statuses().get(operation) == "available"

    def get_status(self) -> ToolStatus:
        return self._probe()[0]

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0

    def estimate_runtime(self, inputs: dict[str, Any]) -> float:
        # Studio loads a 20B-class denoiser plus its text encoder on first use;
        # the measured H3 probe on this class of machine is ~2-3 min for 1.6 s
        # at 8 steps, so a 5 s / 20-step clip is a 10-20 min render.
        return 1200.0

    def get_info(self) -> dict[str, Any]:
        info = super().get_info()
        info["setup_offer"] = self.setup_offer
        status, reason, payload = self._probe()
        info["provider_status"] = {
            "server_url": self._client.server_url,
            "status": status.value if hasattr(status, "value") else str(status),
            "reason": reason,
            "operation_statuses": self.operation_statuses(),
            "loaded_model": UnslothClient.loaded_model(payload) if payload else None,
            "auth": "api_key" if UnslothClient.api_key() else (
                "password" if UnslothClient.has_credentials() else "none"
            ),
        }
        return info

    # ------------------------------------------------------------------
    # Execute
    # ------------------------------------------------------------------

    def _log_progress(self, data: dict[str, Any]) -> None:
        """Progress line on phase/step changes, plus a 60s heartbeat.

        Studio pushes a progress payload every poll; logging each one turns a
        twenty-minute render into hundreds of identical lines.
        """
        phase = data.get("phase")
        step, total = data.get("step"), data.get("total")
        key = (phase, step)
        now = time.monotonic()
        if key != getattr(self, "_last_progress_key", None):
            self._last_progress_key = key
            self._last_progress_log = now
            if phase == "denoise" and total:
                print(f"[unsloth_video] {phase} {step}/{total}")
            elif phase:
                print(f"[unsloth_video] {phase}")
            return
        if now - self._last_progress_log >= 60:
            self._last_progress_log = now
            if phase == "denoise" and total:
                eta = data.get("eta_seconds")
                suffix = f" (~{round(eta)}s left)" if isinstance(eta, (int, float)) else ""
                print(f"[unsloth_video] {phase} {step}/{total}{suffix}")

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        prompt = str(inputs.get("prompt") or "").strip()
        if not prompt:
            return ToolResult(success=False, error="prompt is required")

        status, reason, status_payload = self._probe()
        if status == ToolStatus.UNAVAILABLE:
            return ToolResult(success=False, error=reason)
        if status != ToolStatus.AVAILABLE:
            data = {}
            if not UnslothClient.has_credentials():
                data["setup_required"] = True
            if "bootstrap password" in reason.lower() or "rejected the credentials" in reason.lower():
                data["auth_required"] = True
            return ToolResult(success=False, error=reason, data=data)

        operation = inputs.get("operation", "text_to_video")
        defaults = status_payload.get("defaults") or {}
        fps = inputs.get("fps") or defaults.get("fps") or 24

        width = inputs.get("width")
        height = inputs.get("height")
        if (width is None) != (height is None):
            return ToolResult(
                success=False,
                error="Provide both width and height, or neither (family preset).",
            )

        seed = inputs.get("seed")
        if seed is None:
            seed = UnslothClient.random_seed()
        output_path = Path(inputs.get("output_path", f"unsloth_video_{seed}.mp4"))

        payload: dict[str, Any] = {
            "prompt": prompt,
            "seed": int(seed),
            # Headless runs do not need the live latent preview stream.
            "live_preview": False,
        }
        if width is not None and height is not None:
            payload["width"] = snap_dimension(width, defaults)
            payload["height"] = snap_dimension(height, defaults)
        frame_note = None
        if inputs.get("num_frames"):
            requested = int(inputs["num_frames"])
            low = defaults.get("min_num_frames")
            high = defaults.get("max_num_frames")
            if (isinstance(low, int) and requested < low) or (
                isinstance(high, int) and requested > high
            ):
                return ToolResult(
                    success=False,
                    error=(
                        f"{model_family_label(status_payload)} accepts "
                        f"{low if isinstance(low, int) else 1}-"
                        f"{high if isinstance(high, int) else 'many'} frames "
                        f"({round((low or 1) / float(fps), 1)}-"
                        f"{round((high or fps) / float(fps), 1)} s at {fps} fps); "
                        f"requested {requested}. Omit num_frames to use the family "
                        f"default, pass duration for an approximate length, or pick a "
                        f"count inside the range."
                    ),
                )
            snapped = snap_frames(requested, defaults)
            if snapped != requested:
                frame_note = (
                    f"num_frames {requested} snapped to {snapped} on the family's "
                    f"temporal lattice."
                )
            payload["num_frames"] = snapped
        elif inputs.get("duration"):
            requested = round(float(inputs["duration"]) * float(fps))
            snapped = snap_frames(requested, defaults)
            if abs(snapped - requested) > 1:
                frame_note = (
                    f"duration {inputs['duration']}s -> {snapped} frames "
                    f"({round(snapped / float(fps), 2)}s) to satisfy the family's "
                    f"frame lattice/ranges."
                )
            payload["num_frames"] = snapped
        if inputs.get("fps") or defaults.get("fps"):
            payload["fps"] = int(fps)
        for key in ("negative_prompt", "steps", "guidance", "flow_shift", "audio_flow_shift", "model"):
            if inputs.get(key) is not None:
                payload[key] = inputs[key]

        try:
            if operation == "image_to_video":
                ref = inputs.get("reference_image_path") or inputs.get("reference_image_url")
                if not ref:
                    return ToolResult(
                        success=False,
                        error=(
                            "image_to_video requires reference_image_path or "
                            "reference_image_url."
                        ),
                    )
                if status_payload.get("loaded") and status_payload.get(
                    "supports_first_frame"
                ) is False:
                    return ToolResult(
                        success=False,
                        error=(
                            f"The loaded Studio model ({status_payload.get('repo_id')}) does "
                            f"not accept a first frame. Use text_to_video, or load a "
                            f"keyframe-capable model such as MiniMax H3."
                        ),
                    )
                payload["first_frame"] = to_data_url(ref)

            refs = list(inputs.get("reference_image_paths") or [])
            if refs:
                if len(refs) > _MAX_REFERENCE_IMAGES:
                    return ToolResult(
                        success=False,
                        error=f"At most {_MAX_REFERENCE_IMAGES} reference images are supported.",
                    )
                if status_payload.get("loaded") and status_payload.get(
                    "supports_references"
                ) is False:
                    return ToolResult(
                        success=False,
                        error=(
                            f"The loaded Studio model ({status_payload.get('repo_id')}) does "
                            f"not accept reference images."
                        ),
                    )
                payload["reference_images"] = [to_data_url(p) for p in refs]

            started = time.time()
            progress_cb = inputs.get("_progress_callback")

            def _on_progress(data: dict[str, Any]) -> None:
                self._log_progress(data)
                if callable(progress_cb):
                    progress_cb(data)

            path, record = self._client.generate_video(
                payload,
                output_path,
                timeout=int(inputs.get("timeout_seconds", 3600)),
                on_progress=_on_progress,
            )
        except UnslothAuthError as exc:
            return ToolResult(success=False, error=str(exc), data={"auth_required": True})
        except UnslothError as exc:
            return ToolResult(success=False, error=str(exc))
        except Exception as exc:  # noqa: BLE001 - surface transport/IO errors verbatim
            return ToolResult(
                success=False, error=f"Unsloth video generation failed: {exc}"
            )

        wall = round(time.time() - started, 2)
        measured = _probe_media(path)
        try:
            # Refresh: the pre-generation status predates the model auto-load.
            status_payload = self._client.video_status()
        except UnslothError:
            pass
        loaded = UnslothClient.loaded_model(status_payload)
        data: dict[str, Any] = {
            "provider": self.provider,
            "model": record.get("model")
            or loaded.get("display_repo_id")
            or loaded.get("repo_id"),
            "prompt": prompt,
            "operation": operation,
            "output": str(path),
            "format": "mp4",
            "width": measured.get("width", record.get("width")),
            "height": measured.get("height", record.get("height")),
            "fps": measured.get("fps", record.get("fps")),
            "num_frames": measured.get("num_frames", record.get("num_frames")),
            "duration_seconds": measured.get(
                "duration_seconds", record.get("duration_s")
            ),
            "metadata_source": measured.get("metadata_source", "gallery_record"),
            "has_audio": record.get("has_audio"),
            "steps": record.get("steps", inputs.get("steps")),
            "guidance": record.get("guidance", inputs.get("guidance")),
            "video_id": record.get("id"),
            "workflow_provenance": {
                "source": "unsloth_studio",
                "endpoint": "POST /api/inference/video/generate",
                "server_url": self._client.server_url,
                "video_id": record.get("id"),
                "model_status": loaded,
            },
            "timing": {**dict(self._client.last_timing or {}), "wall_s": wall},
        }
        if operation == "image_to_video":
            data["first_frame_source"] = (
                inputs.get("reference_image_path") or inputs.get("reference_image_url")
            )
        if frame_note:
            data["frame_note"] = frame_note
            data["warnings"] = [frame_note]
        if record.get("has_audio") and inputs.get("generate_audio") is False:
            data["audio_note"] = (
                "This family renders audio jointly with video; the request to drop "
                "audio cannot be honored."
            )
        return ToolResult(
            success=True,
            data=data,
            artifacts=[str(path)],
            cost_usd=0.0,
            duration_seconds=wall,
            seed=int(record.get("seed", seed)),
            model=data["model"],
        )


def model_family_label(status: dict[str, Any]) -> str:
    """Human label for the loaded family, for actionable error messages."""
    name = status.get("display_repo_id") or status.get("repo_id") or status.get("family")
    return f"the loaded model ({name})" if name else "the loaded model family"


def _probe_media(path: Path) -> dict[str, Any]:
    """Measure the downloaded clip; keep gallery values when ffprobe fails."""
    probed = probe_output(path) if path.is_file() else {}
    width = probed.get("video_width")
    height = probed.get("video_height")
    fps = probed.get("video_fps")
    duration = probed.get("duration_seconds")
    if not (width and height and fps and duration):
        return {"metadata_source": "gallery_record"}
    return {
        "width": width,
        "height": height,
        "fps": fps,
        "num_frames": probed.get("video_num_frames") or round(duration * fps),
        "duration_seconds": duration,
        "metadata_source": "ffprobe",
    }
