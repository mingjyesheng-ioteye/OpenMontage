"""BrainBook Video lesson renderer — wraps the bb_video Remotion project.

`bb_video` turns a module JSON (slides + narration + assessment) into a branded
lesson video: TTS narration, word-level captions, optional avatar presenter,
music bed, b-roll, then a Remotion render. Its `scripts/build-lesson.sh` owns
that pipeline; this tool runs it from an OpenMontage stage and reports the real
artifact envelope.

The bb_video checkout is found via ``BB_VIDEO_DIR`` (default: sibling
``../bb_video`` next to the OpenMontage repo).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
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
from tools.video._shared import probe_output

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _bb_video_dir() -> Path | None:
    env = os.environ.get("BB_VIDEO_DIR")
    candidates = [Path(env).expanduser()] if env else []
    candidates += [
        _REPO_ROOT.parent / "bb_video",
        Path.home() / "bb_video",
    ]
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if (resolved / "scripts" / "build-lesson.sh").is_file():
            return resolved
    return None


class BbVideoLesson(BaseTool):
    name = "bb_video_lesson"
    version = "0.1.0"
    tier = ToolTier.GENERATE
    capability = "lesson_video"
    provider = "bb_video"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.STOCHASTIC
    runtime = ToolRuntime.LOCAL

    dependencies = []  # checked dynamically in get_status()
    install_instructions = (
        "Keep a bb_video checkout with its dependencies installed:\n"
        "  cd ~/bb_video && npm install\n"
        "  python3 -m venv .avatar-venv && .avatar-venv/bin/pip install -r requirements.txt\n"
        "  python3 -m venv .tts-venv && .tts-venv/bin/pip install kokoro soundfile\n"
        "Set BB_VIDEO_DIR if the checkout is not next to OpenMontage (default ../bb_video).\n"
        "The renderer needs node (nvm or PATH), ffmpeg, and the Remotion browser."
    )
    agent_skills = []

    capabilities = ["lesson_video_render", "avatar_presenter_video"]
    supports = {
        "script_to_video": True,
        "avatar": True,
        "captions": True,
        "music": True,
        "broll": True,
        "quiz": True,
    }
    best_for = [
        "BrainBook-branded lesson modules from a signed-off script and slide plan",
        "narrated training videos with word-level captions and an optional presenter",
        "one render that includes TTS, avatar, music, b-roll and Remotion",
    ]
    not_good_for = [
        "setups without a bb_video checkout and its venvs/node_modules",
        "shot-by-shot cinematic video (use the cinematic pipeline instead)",
    ]
    fallback = "video_compose"
    fallback_tools = ["video_compose", "video_stitch"]

    input_schema = {
        "type": "object",
        "required": ["module_path"],
        "properties": {
            "module_path": {
                "type": "string",
                "description": (
                    "Path to a bb_video module JSON (id, slides[].heading/sub/"
                    "narration/kind, assessment). Written by the script/scene stages."
                ),
            },
            "output_path": {
                "type": "string",
                "description": "Where to write the rendered MP4 (default <module id>.mp4)",
            },
            "voice_provider": {
                "type": "string",
                "enum": ["kokoro", "edge", "elevenlabs", "openai", "azure", "external"],
                "description": "Narration backend (default kokoro, fully local)",
            },
            "voice": {"type": "string", "description": "Voice id (e.g. af_heart, en-US-AvaNeural)"},
            "avatar_provider": {
                "type": "string",
                "enum": [
                    "none", "placeholder", "kenburns", "sadtalker_gpu", "external",
                    "heygen", "d_id", "fal", "replicate", "kling",
                ],
                "description": "Presenter backend (default kenburns; sadtalker_gpu uses the local service)",
            },
            "avatar_options": {
                "type": "object",
                "description": "Provider-specific avatar options (passed as AVATAR_OPTIONS JSON)",
            },
            "portrait_path": {"type": "string", "description": "Presenter portrait image"},
            "presenter_layout": {
                "type": "string",
                "enum": ["pip-br", "side-right", "side-left", "full"],
            },
            "captions": {
                "type": "string",
                "enum": ["auto", "none", "fast"],
                "description": "Caption alignment mode (default auto)",
            },
            "music_provider": {
                "type": "string",
                "enum": ["none", "acestep", "elevenlabs", "replicate", "fal", "openrouter"],
            },
            "music_prompt": {"type": "string"},
            "music_seconds": {"type": "number"},
            "broll_provider": {
                "type": "string",
                "enum": ["none", "wan", "replicate", "fal", "openrouter"],
            },
            "broll_mode": {"type": "string", "enum": ["t2v", "i2v"]},
            "broll_scope": {"type": "string", "enum": ["module", "all"]},
            "broll_prompt_prefix": {"type": "string"},
            "env_overrides": {
                "type": "object",
                "description": "Advanced: extra build-lesson.sh environment variables",
            },
            "timeout_seconds": {
                "type": "integer",
                "description": "Render timeout (default 5400s; long modules take minutes)",
            },
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=8, ram_mb=16000, vram_mb=0, disk_mb=4000, network_required=False,
    )
    retry_policy = RetryPolicy(max_retries=0)
    idempotency_key_fields = [
        "module_path",
        "voice_provider",
        "voice",
        "avatar_provider",
        "music_provider",
        "broll_provider",
    ]
    side_effects = ["writes narration/caption assets into bb_video/public", "writes the MP4"]
    user_visible_verification = [
        "Watch the lesson for lip-sync/caption sync and slide timing",
        "Check the closing recap and quiz questions match the narrated content",
    ]

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def get_status(self) -> ToolStatus:
        repo = _bb_video_dir()
        if repo is None:
            return ToolStatus.UNAVAILABLE
        if not (repo / "scripts" / "build-lesson.sh").is_file():
            return ToolStatus.UNAVAILABLE
        if not (repo / "node_modules" / ".bin" / "remotion").exists():
            return ToolStatus.UNAVAILABLE
        if shutil.which("ffmpeg") is None:
            return ToolStatus.UNAVAILABLE
        return ToolStatus.AVAILABLE

    def get_info(self) -> dict[str, Any]:
        info = super().get_info()
        repo = _bb_video_dir()
        info["bb_video_dir"] = str(repo) if repo else None
        info["status_reason"] = (
            "" if self.get_status() == ToolStatus.AVAILABLE
            else "bb_video checkout, node_modules/remotion, or ffmpeg is missing"
        )
        return info

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0  # local render; cloud avatar/music providers bill separately

    def estimate_runtime(self, inputs: dict[str, Any]) -> float:
        return 240.0

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        repo = _bb_video_dir()
        if repo is None:
            return ToolResult(success=False, error=self.install_instructions)

        module_path = Path(inputs["module_path"]).expanduser()
        if not module_path.is_absolute():
            module_path = (Path.cwd() / module_path).resolve()
        if not module_path.is_file():
            return ToolResult(success=False, error=f"Module not found: {module_path}")

        try:
            module = json.loads(module_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return ToolResult(success=False, error=f"Invalid module JSON: {exc}")

        slides = module.get("slides") or []
        if not slides:
            return ToolResult(
                success=False,
                error="The module has no slides. The script stage must write "
                      "slides[] with heading, narration and kind before rendering.",
            )
        module_id = str(module.get("id") or "").strip()
        if not module_id:
            return ToolResult(
                success=False,
                error="The module needs an `id` (bb_video writes narration assets "
                      "under public/bb_exam/<id>/).",
            )

        output_path = Path(
            inputs.get("output_path") or f"{module_id}.mp4"
        ).expanduser()
        if not output_path.is_absolute():
            output_path = (Path.cwd() / output_path).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)

        env = self._build_env(inputs)
        started = time.time()
        try:
            proc = subprocess.run(
                ["bash", str(repo / "scripts" / "build-lesson.sh"), str(module_path), str(output_path)],
                cwd=str(repo),
                env=env,
                capture_output=True,
                text=True,
                timeout=int(inputs.get("timeout_seconds", 5400)),
            )
        except subprocess.TimeoutExpired as exc:
            tail = _tail(getattr(exc, "stdout", "") or "", getattr(exc, "stderr", "") or "")
            return ToolResult(
                success=False,
                error=f"bb_video render timed out after {inputs.get('timeout_seconds', 5400)}s.\n{tail}",
            )

        log = _tail(proc.stdout, proc.stderr)
        if proc.returncode != 0:
            return ToolResult(
                success=False,
                error=f"bb_video build failed (exit {proc.returncode}).\n{log}",
            )
        if not output_path.is_file() or output_path.stat().st_size == 0:
            return ToolResult(
                success=False,
                error=f"bb_video reported success but wrote no video at {output_path}.\n{log}",
            )

        probed = probe_output(output_path)
        data: dict[str, Any] = {
            "provider": self.provider,
            "model": "bb_video-remotion",
            "output": str(output_path),
            "module_id": module_id,
            "course_title": module.get("courseTitle"),
            "module_title": module.get("moduleTitle"),
            "slides": len(slides),
            "voice_provider": env.get("VOICE_PROVIDER"),
            "voice": env.get("VOICE"),
            "avatar_provider": env.get("AVATAR_PROVIDER"),
            "music_provider": env.get("MUSIC_PROVIDER"),
            "broll_provider": env.get("BROLL_PROVIDER"),
            "captions": env.get("CAPTIONS_PROVIDER"),
            "render_seconds": round(time.time() - started, 1),
            "log_tail": log[-2500:],
            "probe": probed,
        }
        if probed:
            data.update({
                "width": probed.get("video_width"),
                "height": probed.get("video_height"),
                "fps": probed.get("video_fps"),
                "duration_seconds": probed.get("duration_seconds"),
            })

        quiz_path = repo / "public" / "bb_exam" / module_id / "quiz-result.json"
        if quiz_path.is_file():
            try:
                data["quiz_result"] = json.loads(quiz_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass

        return ToolResult(
            success=True,
            data=data,
            artifacts=[str(output_path)],
            cost_usd=0.0,
            duration_seconds=data["render_seconds"],
            model="bb_video-remotion",
        )

    @staticmethod
    def _build_env(inputs: dict[str, Any]) -> dict[str, str]:
        env = dict(os.environ)
        mapping = {
            "voice_provider": "VOICE_PROVIDER",
            "voice": "VOICE",
            "avatar_provider": "AVATAR_PROVIDER",
            "portrait_path": "PORTRAIT",
            "presenter_layout": "PRESENTER_LAYOUT",
            "captions": "CAPTIONS_PROVIDER",
            "music_provider": "MUSIC_PROVIDER",
            "music_prompt": "MUSIC_PROMPT",
            "music_seconds": "MUSIC_SECONDS",
            "broll_provider": "BROLL_PROVIDER",
            "broll_mode": "BROLL_MODE",
            "broll_scope": "BROLL_SCOPE",
            "broll_prompt_prefix": "BROLL_PROMPT_PREFIX",
        }
        for key, env_key in mapping.items():
            value = inputs.get(key)
            if value not in (None, ""):
                env[env_key] = str(value)
        if inputs.get("avatar_options"):
            env["AVATAR_OPTIONS"] = json.dumps(inputs["avatar_options"])
        for key, value in (inputs.get("env_overrides") or {}).items():
            if value is not None:
                env[str(key)] = str(value)
        return env


def _tail(stdout: str, stderr: str, limit: int = 3000) -> str:
    text = (stdout or "") + ("\n" + stderr if stderr else "")
    text = text.strip()
    return text[-limit:]
