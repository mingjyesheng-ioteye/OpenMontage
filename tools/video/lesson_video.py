"""Native lesson renderer for the BrainBook Video pipeline.

Turns a lesson module JSON (slides + narration + assessment) into a branded,
narrated, captioned lesson video **inside OpenMontage** — no external renderer
checkout. The stages are:

1. narration per slide through an OpenMontage TTS tool (``piper`` locally by
   default, or ``azure`` / ``elevenlabs`` / ``openai`` when configured),
2. real audio durations measured with ``ffprobe`` (the timeline is narration-
   driven, not estimated),
3. word-level captions from provider timings (ElevenLabs alignment), from
   ``transcriber`` when faster-whisper is installed, or proportionally timed as a
   documented fallback,
4. a Remotion render of the ``LessonVideo`` composition, which owns the deck
   layout, presenter frame, progress and knowledge-check cards.

The module JSON stays the contract between the script stage and the renderer, so
a lesson can be re-rendered with another voice or presenter without rewriting
the content.
"""

from __future__ import annotations

import json
import hashlib
import os
import re
import secrets
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

_TTS_PROVIDERS: dict[str, tuple[str, str]] = {
    "piper": ("tools.audio.piper_tts", "PiperTTS"),
    "azure": ("tools.audio.azure_tts", "AzureTTS"),
    "elevenlabs": ("tools.audio.elevenlabs_tts", "ElevenLabsTTS"),
    "openai": ("tools.audio.openai_tts", "OpenAITTS"),
}

_DEFAULT_VOICES = {
    "piper": "en_US-lessac-medium",
    "azure": "en-US-AndrewMultilingualNeural",
    "elevenlabs": "21m00Tcm4TlvDq8ikWAM",  # Rachel
    "openai": "alloy",
}

_AUDIO_SUFFIX = {"piper": ".wav", "azure": ".mp3", "elevenlabs": ".mp3", "openai": ".mp3"}

_WORD_RE = re.compile(r"\S+")
_SENTENCE_END = re.compile(r"[.!?;:]$")


# ----------------------------------------------------------------------
# Environment helpers
# ----------------------------------------------------------------------


def _composer_dir() -> Path:
    return _REPO_ROOT / "remotion-composer"


def _node_bin_dir() -> Path | None:
    """Locate the nvm-installed Node bin dir (non-interactive shells miss it)."""
    candidates: list[Path] = []
    env = os.environ.get("NODE_BIN_DIR")
    if env:
        candidates.append(Path(env).expanduser())
    nvm_root = Path.home() / ".nvm" / "versions" / "node"
    if nvm_root.is_dir():
        candidates.extend(sorted(nvm_root.glob("*/bin"), reverse=True))
    for candidate in candidates:
        if (candidate / "npx").is_file():
            return candidate
    return None


def _node_env() -> dict[str, str]:
    env = dict(os.environ)
    bin_dir = _node_bin_dir()
    if bin_dir is not None:
        env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    return env


def _npx() -> str | None:
    bin_dir = _node_bin_dir()
    if bin_dir is not None and (bin_dir / "npx").is_file():
        return str(bin_dir / "npx")
    return shutil.which("npx")


def _ffprobe_duration_ms(path: Path) -> int | None:
    if shutil.which("ffprobe") is None:
        return None
    try:
        proc = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", str(path),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if proc.returncode != 0:
            return None
        return int(float(proc.stdout.strip()) * 1000)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


# ----------------------------------------------------------------------
# Captions
# ----------------------------------------------------------------------


def _estimated_word_timings(text: str, duration_ms: int) -> list[dict[str, Any]]:
    """Proportional word timings — the honest fallback when no aligner exists.

    Longer words take proportionally longer, and page breaks follow sentence
    punctuation so a caption page does not straddle two sentences.
    """
    words = _WORD_RE.findall(text or "")
    if not words or duration_ms <= 0:
        return []
    weights = [max(2, len(word) + 1) for word in words]
    total = sum(weights)
    out: list[dict[str, Any]] = []
    cursor = 0.0
    for word, weight in zip(words, weights):
        start = cursor
        end = cursor + weight
        cursor = end
        entry = {
            "word": word,
            "startMs": int(round(start / total * duration_ms)),
            "endMs": int(round(end / total * duration_ms)),
        }
        if _SENTENCE_END.search(word):
            entry["pageBreakAfter"] = True
        out.append(entry)
    return out


def _transcribed_word_timings(audio_path: Path, model: str) -> list[dict[str, Any]] | None:
    """Word timings from ``transcriber`` (faster-whisper), or None if absent."""
    try:
        from tools.analysis.transcriber import Transcriber
    except Exception:  # noqa: BLE001 - optional dependency
        return None
    tool = Transcriber()
    if tool.get_status() != ToolStatus.AVAILABLE:
        return None
    try:
        result = tool.execute(
            {"input_path": str(audio_path), "model_size": model, "diarize": False}
        )
    except Exception:  # noqa: BLE001 - transcription is best-effort
        return None
    if not result.success:
        return None
    words = (result.data or {}).get("word_timestamps") or []
    return [
        {
            "word": str(word.get("word") or "").strip(),
            "startMs": int(round(float(word.get("start") or 0) * 1000)),
            "endMs": int(round(float(word.get("end") or 0) * 1000)),
        }
        for word in words
        if str(word.get("word") or "").strip()
    ] or None


def _provider_word_timings(data: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Word timings a TTS provider returned with the audio (ElevenLabs)."""
    words = data.get("word_timestamps") or []
    out = []
    for word in words:
        text = str(word.get("word") or word.get("text") or "").strip()
        if not text:
            continue
        out.append({
            "word": text,
            "startMs": int(round(float(word.get("start") or 0) * 1000)),
            "endMs": int(round(float(word.get("end") or 0) * 1000)),
        })
    return out or None


# ----------------------------------------------------------------------
# Media helpers (avatar track, b-roll trimming)
# ----------------------------------------------------------------------

_SAMPLE_RATE = 24000


def _load_repo_env() -> None:
    """Load OpenMontage's .env so stock/avatar keys are visible."""
    try:
        from tools.base_tool import _load_dotenv

        _load_dotenv()
    except Exception:  # noqa: BLE001 - keys are optional here
        pass


def _ffmpeg(args: list[str], timeout: int = 600) -> bool:
    if shutil.which("ffmpeg") is None:
        return False
    try:
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def _to_wav(source: Path, destination: Path) -> bool:
    return _ffmpeg([
        "-y", "-i", str(source), "-vn", "-ac", "1", "-ar", str(_SAMPLE_RATE),
        "-c:a", "pcm_s16le", str(destination),
    ])


def _silence_wav(destination: Path, duration_ms: int) -> bool:
    if duration_ms <= 0:
        return False
    return _ffmpeg([
        "-y", "-f", "lavfi", "-i",
        f"anullsrc=r={_SAMPLE_RATE}:cl=mono",
        "-t", f"{duration_ms / 1000:.3f}", "-c:a", "pcm_s16le", str(destination),
    ])


def _build_narration_track(
    timeline: list[dict[str, Any]],
    quiz_ms: int,
    work_dir: Path,
    output_path: Path,
) -> bool:
    """Concatenate narration + silence so the avatar matches the video timeline.

    Each slide contributes its narration audio followed by the same padding the
    composition uses; the knowledge-check section contributes silence. The
    result is one continuous track whose length equals the rendered video.
    """
    segments_dir = work_dir / "avatar-track"
    segments_dir.mkdir(parents=True, exist_ok=True)
    segments: list[Path] = []
    for position, item in enumerate(timeline, start=1):
        audio_file: Path | None = item.get("audio_file")
        audio_ms = _ffprobe_duration_ms(audio_file) if audio_file else 0
        if audio_file is not None and audio_ms:
            segment = segments_dir / f"s{position:02d}.wav"
            if not segment.exists() and not _to_wav(audio_file, segment):
                return False
            segments.append(segment)
        gap = int(item["durationMs"]) - audio_ms
        if gap > 0:
            silence = segments_dir / f"gap{position:02d}.wav"
            if not silence.exists() and not _silence_wav(silence, gap):
                return False
            segments.append(silence)
    if quiz_ms > 0:
        silence = segments_dir / "quiz.wav"
        if not silence.exists() and not _silence_wav(silence, quiz_ms):
            return False
        segments.append(silence)
    if not segments:
        return False

    concat_list = segments_dir / "concat.txt"
    concat_list.write_text(
        "".join(f"file '{segment.as_posix()}'\n" for segment in segments),
        encoding="utf-8",
    )
    os.makedirs(output_path.parent, exist_ok=True)
    return _ffmpeg([
        "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list),
        "-c", "copy", str(output_path),
    ])


def _trim_broll(source: Path, destination: Path, duration_ms: int, width: int = 1280) -> bool:
    """Loop/trim a clip to the slide length, strip audio, normalise the width."""
    seconds = max(1.0, duration_ms / 1000)
    return _ffmpeg([
        "-y", "-stream_loop", "-1", "-i", str(source), "-t", f"{seconds:.3f}",
        "-an", "-vf", f"scale={width}:-2:flags=lanczos",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
        "-pix_fmt", "yuv420p", str(destination),
    ], timeout=900)


_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


def _normalize_comfy_url() -> None:
    """WSL resolves ``localhost`` to ::1, which the Windows ComfyUI doesn't bind."""
    for key in ("COMFYUI_SERVER_URL", "COMFYUI_IMAGE_SERVER_URL"):
        value = os.environ.get(key)
        if value and "localhost" in value:
            os.environ[key] = value.replace("localhost", "127.0.0.1")


def _comfy_image_family(tool: Any) -> str | None:
    """First ComfyUI image family whose models are fully installed."""
    try:
        from tools.graphics.comfyui_image import _MODEL_FAMILIES
    except Exception:  # noqa: BLE001 - optional module
        return None
    for family, spec in _MODEL_FAMILIES.items():
        try:
            if not tool._client.check_models(spec["required_models"])[1]:
                return family
        except Exception:  # noqa: BLE001
            continue
    return None


def _generate_broll_image(prompt: str, output_path: Path) -> bool:
    """Local still for the b-roll panel (ComfyUI first, then Unsloth Studio)."""
    _normalize_comfy_url()
    try:
        from tools.graphics.comfyui_image import ComfyUIImage

        tool = ComfyUIImage()
        if tool.get_status() != ToolStatus.UNAVAILABLE:
            inputs: dict[str, Any] = {
                "prompt": prompt,
                "width": 1024,
                "height": 1024,
                "steps": 8,
                "output_path": str(output_path),
            }
            family = _comfy_image_family(tool)
            if family:
                inputs["model_family"] = family
            result = tool.execute(inputs)
            if result.success and output_path.is_file():
                return True
    except Exception:  # noqa: BLE001 - fall through to the Studio backend
        pass

    try:
        from tools.graphics.unsloth_image import UnslothImage

        studio = UnslothImage()
        if studio.get_status() == ToolStatus.AVAILABLE:
            result = studio.execute({
                "prompt": prompt,
                "width": 1024,
                "height": 1024,
                "steps": 8,
                "output_path": str(output_path),
            })
            return bool(result.success and output_path.is_file())
    except Exception:  # noqa: BLE001
        pass
    return False


def _course_context(
    module: dict[str, Any],
    source: dict[str, Any] | None,
    questions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Merge course information from the module and a bb_exam source payload."""
    source = source or {}
    module_source = source.get("module") or {}
    objectives = module.get("objectives") or module_source.get("objectives") or []
    passing = (
        source.get("passing_percent")
        or source.get("passing_threshold")
        or module.get("passingPercent")
        or (module.get("assessment") or {}).get("passingPercent")
        or (module.get("assessment") or {}).get("passing_percent")
    )
    if isinstance(passing, (int, float)) and 0 < passing <= 1:
        passing = round(passing * 100)
    module_index = (
        module.get("moduleIndex")
        or module_source.get("module_index")
        or module_source.get("order")
        or source.get("module_index")
    )
    module_total = module.get("moduleTotal") or source.get("module_total")
    quiz_count = len(questions) or len(module_source.get("quiz") or [])
    return {
        "title": (
            source.get("course_title")
            or module.get("courseTitle")
            or module.get("course_title")
        ),
        "description": (
            source.get("course_description")
            or module.get("courseDescription")
            or module.get("course_description")
        ),
        "level": module.get("level") or module_source.get("level"),
        "moduleIndex": module_index,
        "moduleTotal": module_total,
        "summary": module_source.get("summary") or module.get("moduleSummary"),
        "objectives": objectives if isinstance(objectives, list) else [],
        "passingPercent": passing,
        "quizCount": quiz_count,
    }


def _read_source(module_path: Path, explicit: str | None) -> dict[str, Any] | None:
    candidate = Path(str(explicit)).expanduser() if explicit else module_path.parent / "bb_exam_source.json"
    if not candidate.is_absolute():
        candidate = (Path.cwd() / candidate).resolve()
    if not candidate.is_file():
        return None
    try:
        payload = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


# ----------------------------------------------------------------------
# Narration
# ----------------------------------------------------------------------


def _tts_tool(provider: str) -> BaseTool:
    module_name, class_name = _TTS_PROVIDERS[provider]
    module = __import__(module_name, fromlist=[class_name])
    return getattr(module, class_name)()


def _narrate(
    provider: str,
    text: str,
    voice: str | None,
    rate: float,
    output_path: Path,
) -> tuple[BaseTool, ToolResult]:
    """Synthesize one slide's narration through the provider's OpenMontage tool."""
    tool = _tts_tool(provider)
    voice = voice or _DEFAULT_VOICES[provider]
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if provider == "piper":
        inputs = {
            "text": text,
            "output_path": str(output_path),
            "model": voice,
            # piper's length_scale is the inverse of speaking rate.
            "length_scale": round(1.0 / rate, 3),
        }
    elif provider == "azure":
        inputs = {
            "text": text,
            "output_path": str(output_path),
            "voice": voice,
            "rate": f"{(rate - 1.0) * 100:+.0f}%",
        }
    elif provider == "elevenlabs":
        inputs = {
            "text": text,
            "output_path": str(output_path),
            "voice_id": voice,
            "speed": max(0.7, min(1.2, rate)),
            "with_timestamps": True,
        }
    else:  # openai
        inputs = {
            "text": text,
            "output_path": str(output_path),
            "voice": voice,
            "speed": max(0.25, min(4.0, rate)),
        }
    return tool, tool.execute(inputs)


# ----------------------------------------------------------------------
# Module normalization
# ----------------------------------------------------------------------


def _slide_text(slide: dict[str, Any]) -> str:
    return str(slide.get("narration") or "").strip()


def _slide_bullets(slide: dict[str, Any]) -> list[str]:
    for key in ("bullets", "on_screen_text", "points"):
        values = slide.get(key)
        if isinstance(values, list):
            cleaned = [str(value).strip() for value in values if str(value).strip()]
            if cleaned:
                return cleaned
    return []


def _normalize_slides(module: dict[str, Any]) -> list[dict[str, Any]]:
    slides = module.get("slides") or []
    normalized = []
    for index, slide in enumerate(slides):
        if not isinstance(slide, dict):
            continue
        kind = str(slide.get("kind") or "content").strip().lower()
        if kind not in {"title", "objectives", "content", "recap", "quiz"}:
            kind = "content"
        normalized.append({
            "kind": kind,
            "heading": str(slide.get("heading") or slide.get("title") or "").strip(),
            "sub": (str(slide["sub"]).strip() if slide.get("sub") else None),
            "bullets": _slide_bullets(slide),
            "narration": _slide_text(slide),
            "broll_path": slide.get("broll_path") or slide.get("broll"),
            "broll_query": slide.get("broll_query"),
            "_index": index,
        })
    return normalized


# ----------------------------------------------------------------------
# Tool
# ----------------------------------------------------------------------


class LessonVideo(BaseTool):
    name = "lesson_video"
    version = "1.0.0"
    tier = ToolTier.GENERATE
    capability = "lesson_video"
    provider = "openmontage"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.STOCHASTIC
    runtime = ToolRuntime.LOCAL

    dependencies = []  # checked dynamically in get_status()
    install_instructions = (
        "The lesson renderer needs Node.js (npx + remotion-composer/node_modules), "
        "ffmpeg/ffprobe, and at least one TTS provider:\n"
        "  pnpm --dir remotion-composer install   # or npm install\n"
        "  pip install piper-tts && python -m piper.download_voices en_US-lessac-medium\n"
        "Cloud voices need AZURE_SPEECH_KEY, ELEVENLABS_API_KEY, or OPENAI_API_KEY."
    )
    agent_skills = ["text-to-speech", "ffmpeg"]

    capabilities = ["lesson_video_render", "narrated_slides", "word_captions", "lip_sync", "broll"]
    supports = {
        "script_to_video": True,
        "captions": True,
        "music": True,
        "quiz": True,
        "presenter": True,
        "avatar": True,
        "lip_sync": True,
        "broll": True,
        "course_info": True,
    }
    best_for = [
        "BrainBook-branded lesson modules from a signed-off module JSON",
        "narrated training decks with word-level captions and a knowledge check",
        "a lip-synced presenter and stock b-roll on the same local render",
    ]
    not_good_for = [
        "shot-by-shot cinematic video (use the cinematic pipeline)",
        "multi-speaker conversation video (this renderer is one presenter)",
    ]
    fallback = "video_compose"
    fallback_tools = ["video_compose", "remotion_caption_burn"]

    input_schema = {
        "type": "object",
        "required": ["module_path"],
        "properties": {
            "module_path": {
                "type": "string",
                "description": (
                    "Lesson module JSON: id, courseTitle, moduleTitle, "
                    "slides[].heading/sub/narration/kind/bullets, assessment."
                ),
            },
            "output_path": {
                "type": "string",
                "description": "Rendered MP4 path (default <module id>.mp4)",
            },
            "voice_provider": {
                "type": "string",
                "enum": list(_TTS_PROVIDERS),
                "description": "Narration backend (default piper, fully local)",
            },
            "voice": {
                "type": "string",
                "description": (
                    "Voice id for the provider: piper model name "
                    "(en_US-lessac-medium), Azure short name, ElevenLabs voice id, "
                    "or an OpenAI voice."
                ),
            },
            "rate": {
                "type": "number",
                "minimum": 0.7,
                "maximum": 1.3,
                "description": "Speaking rate multiplier (default 1.0)",
            },
            "captions": {
                "type": "string",
                "enum": ["auto", "none"],
                "description": (
                    "auto (default): provider timings, else faster-whisper, else "
                    "proportional word timing. none: no burned-in captions."
                ),
            },
            "caption_model": {
                "type": "string",
                "description": "faster-whisper model when transcription is used (default base)",
            },
            "music_path": {"type": "string", "description": "Optional music bed audio file"},
            "music_volume": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "description": "Music bed level under narration (default 0.12)",
            },
            "portrait_path": {"type": "string", "description": "Optional presenter portrait"},
            "avatar_provider": {
                "type": "string",
                "enum": ["auto", "none"],
                "description": (
                    "auto (default): lip-sync the portrait with the talking-head "
                    "service when it is running, else fall back to a still frame. "
                    "none: still portrait only."
                ),
            },
            "avatar_engine": {
                "type": "string",
                "enum": ["wav2lip", "musetalk", "sadtalker"],
                "description": "Talking-head engine (default wav2lip, the fastest)",
            },
            "presenter_layout": {
                "type": "string",
                "enum": ["none", "pip-br", "side-right", "full"],
                "description": "Presenter frame (default pip-br when a portrait is given)",
            },
            "presenter_name": {"type": "string"},
            "presenter_role": {"type": "string"},
            "broll_provider": {
                "type": "string",
                "enum": ["auto", "none", "stock", "generated", "files"],
                "description": (
                    "auto (default): supplied file, then stock footage when a "
                    "Pexels/Pixabay key exists, then local ComfyUI stills. "
                    "Each mode can be forced; files uses module broll_path."
                ),
            },
            "broll_layout": {
                "type": "string",
                "enum": ["panel", "background"],
                "description": "How b-roll is framed on content slides (default panel)",
            },
            "broll_scope": {
                "type": "string",
                "enum": ["content", "all"],
                "description": "Which slides get b-roll (default content)",
            },
            "source_path": {
                "type": "string",
                "description": (
                    "Optional bb_exam_source.json with course description, module "
                    "position and passing threshold. Auto-detected next to the module."
                ),
            },
            "theme": {
                "type": "string",
                "description": "Playbook/theme name resolved by the Remotion composer",
            },
            "accent_color": {"type": "string", "description": "Override accent color (#RRGGBB)"},
            "fps": {"type": "integer", "minimum": 12, "maximum": 60},
            "width": {"type": "integer", "minimum": 320},
            "height": {"type": "integer", "minimum": 320},
            "show_assessment": {
                "type": "boolean",
                "description": "Append knowledge-check cards from assessment.questions (default true)",
            },
            "quiz_seconds": {"type": "number", "description": "Seconds per question (default 7)"},
            "caption_words_per_page": {"type": "integer", "minimum": 1, "maximum": 8},
            "remotion_concurrency": {"type": "integer"},
            "timeout_seconds": {"type": "integer", "description": "Render timeout (default 3600s)"},
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=4, ram_mb=8000, vram_mb=0, disk_mb=4000, network_required=False,
    )
    retry_policy = RetryPolicy(max_retries=0)
    idempotency_key_fields = [
        "module_path",
        "output_path",
        "voice_provider",
        "voice",
        "rate",
        "captions",
        "music_path",
        "portrait_path",
    ]
    side_effects = ["writes narration audio next to the output", "writes the MP4"]
    user_visible_verification = [
        "Watch the lesson: narration, captions and slide changes stay in sync",
        "Check the knowledge-check answers match the narrated content",
    ]

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def _available_tts(self) -> list[str]:
        out = []
        for provider in _TTS_PROVIDERS:
            try:
                if _tts_tool(provider).get_status() == ToolStatus.AVAILABLE:
                    out.append(provider)
            except Exception:  # noqa: BLE001 - a broken adapter is just unavailable
                continue
        return out

    def get_status(self) -> ToolStatus:
        if shutil.which("ffprobe") is None or shutil.which("ffmpeg") is None:
            return ToolStatus.UNAVAILABLE
        if _npx() is None:
            return ToolStatus.UNAVAILABLE
        remotion = _composer_dir() / "node_modules" / ".bin" / "remotion"
        if not remotion.exists():
            return ToolStatus.UNAVAILABLE
        if not self._available_tts():
            return ToolStatus.UNAVAILABLE
        return ToolStatus.AVAILABLE

    def get_info(self) -> dict[str, Any]:
        info = super().get_info()
        info["render_composition"] = "LessonVideo"
        info["tts_providers"] = self._available_tts()
        info["avatar_service"] = self._avatar_available()
        info["broll_sources"] = self._broll_sources()
        info["status_reason"] = (
            "" if self.get_status() == ToolStatus.AVAILABLE
            else "needs Node/npx + remotion-composer, ffmpeg/ffprobe, and a TTS provider"
        )
        return info

    @staticmethod
    def _avatar_available() -> bool:
        try:
            from tools.avatar.talking_head import TalkingHead

            return TalkingHead().get_status() == ToolStatus.AVAILABLE
        except Exception:  # noqa: BLE001
            return False

    @staticmethod
    def _broll_sources() -> list[str]:
        """Which b-roll sources this host can actually use right now."""
        sources = ["files"]
        _load_repo_env()
        if os.environ.get("PEXELS_API_KEY") or os.environ.get("PIXABAY_API_KEY"):
            sources.append("stock")
        try:
            from tools.graphics.comfyui_image import ComfyUIImage

            _normalize_comfy_url()
            if ComfyUIImage().get_status() != ToolStatus.UNAVAILABLE:
                sources.append("generated")
        except Exception:  # noqa: BLE001
            pass
        if "generated" not in sources:
            try:
                from tools.graphics.unsloth_image import UnslothImage

                if UnslothImage().get_status() == ToolStatus.AVAILABLE:
                    sources.append("generated")
            except Exception:  # noqa: BLE001
                pass
        return sources

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0  # piper + Remotion are local; cloud voices bill in their own tool

    def estimate_runtime(self, inputs: dict[str, Any]) -> float:
        return 300.0

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        module_path = Path(inputs["module_path"]).expanduser()
        if not module_path.is_absolute():
            module_path = (Path.cwd() / module_path).resolve()
        if not module_path.is_file():
            return ToolResult(success=False, error=f"Module not found: {module_path}")

        try:
            module = json.loads(module_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return ToolResult(success=False, error=f"Invalid module JSON: {exc}")

        slides = _normalize_slides(module)
        if not slides:
            return ToolResult(
                success=False,
                error="The module has no slides. The script stage must write "
                      "slides[] with heading, narration and kind before rendering.",
            )
        module_id = str(module.get("id") or module_path.stem).strip()
        if not module_id:
            return ToolResult(success=False, error="The module needs an `id`.")

        provider = str(inputs.get("voice_provider") or "piper")
        if provider not in _TTS_PROVIDERS:
            return ToolResult(
                success=False,
                error=f"Unknown voice_provider {provider!r}. Choose {sorted(_TTS_PROVIDERS)}.",
            )
        rate = float(inputs.get("rate") or 1.0)
        captions_mode = str(inputs.get("captions") or "auto").lower()

        output_path = Path(inputs.get("output_path") or f"{module_id}.mp4").expanduser()
        if not output_path.is_absolute():
            output_path = (Path.cwd() / output_path).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)

        assets_dir = output_path.parent / f"{module_id}-lesson-assets"
        narration_dir = assets_dir / "narration"
        narration_dir.mkdir(parents=True, exist_ok=True)

        started = time.time()
        timeline: list[dict[str, Any]] = []
        warnings: list[str] = []
        narration_ms = 0

        for position, slide in enumerate(slides, start=1):
            text = slide["narration"]
            audio_path: Path | None = None
            audio_ms = 0
            words: list[dict[str, Any]] | None = None

            if text:
                suffix = _AUDIO_SUFFIX[provider]
                audio_path = narration_dir / f"s{position:02d}{suffix}"
                if audio_path.exists() and audio_path.stat().st_size > 0:
                    # Resume: narration is the expensive stage; keep it.
                    pass
                else:
                    tool, result = _narrate(
                        provider, text, inputs.get("voice"), rate, audio_path
                    )
                    if not result.success:
                        return ToolResult(
                            success=False,
                            error=(
                                f"Narration failed on slide {position} "
                                f"({provider}/{inputs.get('voice') or _DEFAULT_VOICES[provider]}): "
                                f"{result.error}"
                            ),
                        )
                    if provider == "elevenlabs":
                        words = _provider_word_timings(result.data or {})
                audio_ms = _ffprobe_duration_ms(audio_path) or 0
                if audio_ms <= 0:
                    return ToolResult(
                        success=False,
                        error=f"ffprobe found no audio in {audio_path} (slide {position}).",
                    )
            narration_ms += audio_ms

            if captions_mode != "none" and text:
                if words is None and audio_path is not None:
                    words = _transcribed_word_timings(
                        audio_path, str(inputs.get("caption_model") or "base")
                    )
                if words is None:
                    words = _estimated_word_timings(text, audio_ms)
                    if not any("proportional" in warning for warning in warnings):
                        warnings.append(
                            "captions: proportional word timing "
                            "(install faster-whisper for aligned timings)"
                        )
                # Clamp to the audio and keep entries ordered.
                words = [
                    {
                        **word,
                        "startMs": max(0, min(audio_ms, word["startMs"])),
                        "endMs": max(0, min(audio_ms, word["endMs"])),
                    }
                    for word in words
                    if word["endMs"] > word["startMs"]
                ]

            padding_ms = 550
            duration_ms = max(
                2000,
                audio_ms + padding_ms if audio_ms else 4500,
            )
            if slide["kind"] == "title":
                duration_ms = max(duration_ms, 3800)

            timeline.append({
                "kind": slide["kind"],
                "heading": slide["heading"],
                "sub": slide["sub"],
                "bullets": slide["bullets"],
                "narration": text,
                "audio_file": audio_path,
                "durationMs": duration_ms,
                "caption_words": words or [],
            })

        # --------------------------------------------------------------
        # Knowledge check + course context
        # --------------------------------------------------------------
        assessment = module.get("assessment") or None
        questions: list[dict[str, Any]] = []
        for question in (assessment or {}).get("questions") or []:
            if not isinstance(question, dict):
                continue
            prompt = question.get("q") or question.get("prompt") or question.get("question")
            options = question.get("options") or []
            if not prompt or not isinstance(options, list):
                continue
            options = [
                str(option.get("text") if isinstance(option, dict) else option)
                for option in options
            ]
            answer = question.get("answer")
            questions.append({
                "q": str(prompt),
                "options": options,
                "answer": answer if isinstance(answer, int) else None,
            })

        show_assessment = inputs.get("show_assessment", True) is not False
        quiz_seconds = float(inputs.get("quiz_seconds") or 7)
        quiz_ms = round(len(questions) * quiz_seconds * 1000) if show_assessment else 0

        source_payload = _read_source(module_path, inputs.get("source_path"))
        course = _course_context(module, source_payload, questions)

        # --------------------------------------------------------------
        # B-roll — supplied files, stock footage, or local generated stills
        # --------------------------------------------------------------
        broll_provider = str(inputs.get("broll_provider") or "auto").lower()
        broll_layout = str(inputs.get("broll_layout") or "panel").lower()
        broll_scope = str(inputs.get("broll_scope") or "content").lower()
        broll_dir = assets_dir / "broll"
        broll_dir.mkdir(parents=True, exist_ok=True)
        # index -> (path, "video" | "image")
        broll_sources: dict[int, tuple[Path, str]] = {}
        broll_cache: dict[str, tuple[Path, str] | None] = {}
        broll_modes: set[str] = set()

        if broll_provider != "none":
            _load_repo_env()
            stock_available = broll_provider in ("auto", "stock") and bool(
                os.environ.get("PEXELS_API_KEY") or os.environ.get("PIXABAY_API_KEY")
            )
            generate_allowed = broll_provider in ("auto", "generated")
            comfy_ready = generate_allowed and "generated" in self._broll_sources()

            selected = [
                index
                for index, slide in enumerate(slides)
                if (broll_scope == "all" and slide["kind"] != "title")
                or (broll_scope == "content" and slide["kind"] == "content")
            ]
            for index in selected:
                slide = slides[index]
                supplied = slide.get("broll_path")
                if supplied:
                    raw = Path(str(supplied)).expanduser()
                    if raw.is_absolute():
                        candidates = [raw]
                    else:
                        # Relative paths may be written from the module's folder
                        # (artifacts/) or from the project root; accept both.
                        candidates = [
                            module_path.parent / raw,
                            module_path.parent.parent / raw,
                            Path.cwd() / raw,
                        ]
                    candidate = next((path for path in candidates if path.is_file()), None)
                    if candidate is not None:
                        kind = "image" if candidate.suffix.lower() in _IMAGE_SUFFIXES else "video"
                        broll_sources[index] = (candidate, kind)
                        broll_modes.add("files")
                        continue
                    warnings.append(
                        f"broll: supplied clip not found: {str(supplied)!r}"
                    )

                query = str(slide.get("broll_query") or slide["heading"]).strip()
                if not query:
                    continue

                # 1) stock footage
                if stock_available:
                    key = f"stock:{query}"
                    if key not in broll_cache:
                        digest = hashlib.sha256(query.encode("utf-8")).hexdigest()[:10]
                        clip = broll_dir / f"stock-{digest}.mp4"
                        resolved: tuple[Path, str] | None = None
                        if clip.is_file() and clip.stat().st_size > 0:
                            resolved = (clip, "video")
                        else:
                            try:
                                from tools.video.pexels_video import PexelsVideo

                                pexels = PexelsVideo()
                                if pexels.get_status() == ToolStatus.AVAILABLE:
                                    result = pexels.execute({
                                        "query": query,
                                        "orientation": "landscape",
                                        "size": "medium",
                                        "preferred_quality": "hd",
                                        "max_duration": 30,
                                        "output_path": str(clip),
                                    })
                                    if result.success and clip.is_file():
                                        resolved = (clip, "video")
                                        broll_modes.add("stock")
                                    else:
                                        warnings.append(
                                            f"broll: no stock clip for {query!r} "
                                            f"({(result.error or '')[:120]})"
                                        )
                            except Exception as exc:  # noqa: BLE001
                                warnings.append(
                                    f"broll: stock search error for {query!r} "
                                    f"({type(exc).__name__})"
                                )
                        broll_cache[key] = resolved
                    cached = broll_cache.get(key)
                    if cached is not None:
                        broll_sources[index] = cached
                        broll_modes.add("stock")
                        continue

                # 2) local generated still (ComfyUI)
                if generate_allowed and comfy_ready:
                    key = f"image:{query}"
                    if key not in broll_cache:
                        digest = hashlib.sha256(query.encode("utf-8")).hexdigest()[:10]
                        image = broll_dir / f"still-{digest}.png"
                        resolved = None
                        if image.is_file() and image.stat().st_size > 0:
                            resolved = (image, "image")
                        else:
                            prompt = (
                                "Editorial photograph for a professional training video"
                                + (f" about {course.get('title')}" if course.get("title") else "")
                                + f": {slide['heading']}."
                                + (f" {slide['sub']}." if slide.get("sub") else "")
                                + " Natural light, realistic, shallow depth of field, "
                                  "candid, no text, no logos, no watermarks."
                            )
                            if _generate_broll_image(prompt, image):
                                resolved = (image, "image")
                                broll_modes.add("generated")
                            else:
                                warnings.append(
                                    f"broll: still generation failed for {query!r}"
                                )
                        broll_cache[key] = resolved
                    cached = broll_cache.get(key)
                    if cached is not None:
                        broll_sources[index] = cached
                        broll_modes.add("generated")
                        continue

            if not broll_sources and broll_provider in ("auto", "stock", "generated"):
                if not stock_available and not comfy_ready:
                    warnings.append(
                        "broll: no source available (add PEXELS_API_KEY, start ComfyUI, "
                        "or supply broll_path per slide)"
                    )

        # --------------------------------------------------------------
        # Presenter lip sync — one clip across the whole narration track
        # --------------------------------------------------------------
        portrait = inputs.get("portrait_path")
        layout = str(inputs.get("presenter_layout") or "")
        avatar_provider = str(inputs.get("avatar_provider") or "auto").lower()
        avatar_engine = str(inputs.get("avatar_engine") or "wav2lip").lower()
        avatar_file: Path | None = None
        avatar_seconds = 0.0
        avatar_used = "none"

        if avatar_provider == "auto" and portrait and layout != "none":
            portrait_file = Path(str(portrait)).expanduser()
            if not portrait_file.is_absolute():
                portrait_file = (Path.cwd() / portrait_file).resolve()
            expected_ms = sum(int(item["durationMs"]) for item in timeline) + quiz_ms
            candidate = assets_dir / "avatar.mp4"
            existing_ms = _ffprobe_duration_ms(candidate) if candidate.is_file() else None
            if candidate.is_file() and existing_ms and abs(existing_ms - expected_ms) < 1200:
                avatar_file = candidate
                avatar_used = "cached"
            else:
                track = assets_dir / "avatar-track.wav"
                if not track.is_file() or track.stat().st_size == 0:
                    built = _build_narration_track(timeline, quiz_ms, assets_dir, track)
                    if not built:
                        warnings.append("avatar: could not build the narration track")
                if track.is_file():
                    try:
                        from tools.avatar.talking_head import TalkingHead

                        talking_head = TalkingHead()
                        if talking_head.get_status() != ToolStatus.AVAILABLE:
                            warnings.append(
                                "avatar: talking-head service unavailable; using a still portrait"
                            )
                        else:
                            avatar_started = time.time()
                            result = talking_head.execute({
                                "image_path": str(portrait_file),
                                "audio_path": str(track),
                                "output_path": str(candidate),
                                "engine": avatar_engine,
                            })
                            avatar_seconds = round(time.time() - avatar_started, 1)
                            if result.success and candidate.is_file():
                                avatar_file = candidate
                                avatar_used = avatar_engine
                            else:
                                warnings.append(
                                    "avatar: lip sync failed "
                                    f"({(result.error or 'unknown error')[:180]}); using a still portrait"
                                )
                    except Exception as exc:  # noqa: BLE001 - never block the lesson
                        warnings.append(f"avatar: lip sync error ({type(exc).__name__}: {exc})")

        # --------------------------------------------------------------
        # Stage media for Remotion (staticFile paths are public-relative)
        # --------------------------------------------------------------
        token = secrets.token_hex(4)
        public_dir = output_path.parent / f".lesson-public-{module_id}-{token}"
        public_dir.mkdir(parents=True, exist_ok=True)
        props_path = output_path.parent / f".lesson-props-{module_id}-{token}.json"
        cleanup = True

        broll_used = 0

        try:
            for position, item in enumerate(timeline, start=1):
                audio_source: Path | None = item.pop("audio_file")
                caption_words = item.pop("caption_words")
                item["captionWords"] = caption_words
                if audio_source is None:
                    item["audioSrc"] = None
                else:
                    name = f"{module_id}-s{position:02d}{audio_source.suffix}"
                    shutil.copy2(audio_source, public_dir / name)
                    item["audioSrc"] = name

                supplied = broll_sources.get(position - 1)
                if supplied is not None:
                    broll_path, broll_kind = supplied
                    if broll_kind == "image":
                        name = f"broll-s{position:02d}{broll_path.suffix.lower()}"
                        shutil.copy2(broll_path, public_dir / name)
                        item["brollSrc"] = name
                        item["brollKind"] = "image"
                        item["brollLayout"] = broll_layout
                        broll_used += 1
                    else:
                        name = f"broll-s{position:02d}.mp4"
                        if _trim_broll(broll_path, public_dir / name, int(item["durationMs"])):
                            item["brollSrc"] = name
                            item["brollKind"] = "video"
                            item["brollLayout"] = broll_layout
                            broll_used += 1
                        else:
                            warnings.append(
                                f"broll: could not trim {broll_path.name} for slide {position}"
                            )

            music_src = None
            music_path = inputs.get("music_path")
            if music_path:
                music_file = Path(str(music_path)).expanduser()
                if not music_file.is_absolute():
                    music_file = (Path.cwd() / music_file).resolve()
                if not music_file.is_file():
                    return ToolResult(
                        success=False, error=f"Music bed not found: {music_file}"
                    )
                shutil.copy2(music_file, public_dir / f"music{music_file.suffix}")
                music_src = f"music{music_file.suffix}"

            presenter = None
            if portrait and layout != "none":
                portrait_file = Path(str(portrait)).expanduser()
                if not portrait_file.is_absolute():
                    portrait_file = (Path.cwd() / portrait_file).resolve()
                if not portrait_file.is_file():
                    return ToolResult(
                        success=False, error=f"Presenter portrait not found: {portrait_file}"
                    )
                shutil.copy2(portrait_file, public_dir / f"portrait{portrait_file.suffix}")
                presenter = {
                    "portraitSrc": f"portrait{portrait_file.suffix}",
                    "layout": layout or "pip-br",
                    "name": inputs.get("presenter_name") or "",
                    "role": inputs.get("presenter_role") or "",
                }
                if avatar_file is not None:
                    shutil.copy2(avatar_file, public_dir / "avatar.mp4")
                    presenter["videoSrc"] = "avatar.mp4"
            elif layout and layout != "none":
                warnings.append("presenter: layout requested without a portrait; skipped")

            fps = int(inputs.get("fps") or 30)
            width = int(inputs.get("width") or 1920)
            height = int(inputs.get("height") or 1080)
            props: dict[str, Any] = {
                "brand": module.get("brand"),
                "courseTitle": course.get("title") or module.get("courseTitle"),
                "moduleTitle": module.get("moduleTitle"),
                "level": course.get("level") or module.get("level"),
                "moduleIndex": course.get("moduleIndex") or module.get("moduleIndex"),
                "moduleTotal": course.get("moduleTotal") or module.get("moduleTotal"),
                "course": course,
                "theme": inputs.get("theme") or "clean-professional",
                "accentColor": inputs.get("accent_color")
                or (module.get("presenter") or {}).get("accentColor"),
                "slides": timeline,
                "musicSrc": music_src,
                "musicVolume": float(inputs.get("music_volume") or 0.12),
                "presenter": presenter,
                "assessment": {"questions": questions} if questions else None,
                "showAssessment": show_assessment,
                "captions": captions_mode != "none",
                "wordsPerPage": int(inputs.get("caption_words_per_page") or 4),
                "quizSeconds": quiz_seconds,
                "fps": fps,
                "width": width,
                "height": height,
            }
            props_path.write_text(json.dumps(props, indent=2), encoding="utf-8")

            command = [
                _npx() or "npx", "remotion", "render",
                "src/index.tsx", "LessonVideo", str(output_path),
                f"--props={props_path}",
                f"--public-dir={public_dir}",
            ]
            if inputs.get("remotion_concurrency"):
                command.append(f"--concurrency={int(inputs['remotion_concurrency'])}")
            if inputs.get("remotion_timeout_ms"):
                command.append(f"--timeout={int(inputs['remotion_timeout_ms'])}")

            timeout = int(inputs.get("timeout_seconds") or 3600)
            try:
                proc = subprocess.run(
                    command,
                    cwd=str(_composer_dir()),
                    env=_node_env(),
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
            except subprocess.TimeoutExpired as exc:
                tail = _tail(getattr(exc, "stdout", "") or "", getattr(exc, "stderr", "") or "")
                return ToolResult(
                    success=False,
                    error=f"Lesson render timed out after {timeout}s.\n{tail}",
                )
            except OSError as exc:
                return ToolResult(success=False, error=f"Could not start Remotion: {exc}")

            log = _tail(proc.stdout or "", proc.stderr or "")
            if proc.returncode != 0:
                return ToolResult(
                    success=False,
                    error=f"Remotion render failed (exit {proc.returncode}).\n{log}",
                )
            if not output_path.is_file() or output_path.stat().st_size == 0:
                return ToolResult(
                    success=False,
                    error=f"Remotion reported success but wrote no video at {output_path}.\n{log}",
                )

            probed = probe_output(output_path)
            render_seconds = round(time.time() - started, 1)
            words_total = sum(len(item["captionWords"]) for item in timeline)
            caption_kind = (
                "none" if captions_mode == "none"
                else "provider" if provider == "elevenlabs"
                else "proportional"
                if any("proportional" in warning for warning in warnings)
                else "transcribed"
            )
            data: dict[str, Any] = {
                "provider": self.provider,
                "model": "lesson-video-remotion",
                "output": str(output_path),
                "module_id": module_id,
                "course_title": course.get("title") or module.get("courseTitle"),
                "module_title": module.get("moduleTitle"),
                "slides": len(timeline),
                "slide_durations_ms": [item["durationMs"] for item in timeline],
                "narration_seconds": round(narration_ms / 1000, 1),
                "voice_provider": provider,
                "voice": inputs.get("voice") or _DEFAULT_VOICES[provider],
                "rate": rate,
                "captions": caption_kind,
                "caption_words": words_total,
                "assessment_questions": len(questions),
                "course": {
                    "title": course.get("title"),
                    "description": bool(course.get("description")),
                    "module_position": (
                        f"{course.get('moduleIndex')} of {course.get('moduleTotal')}"
                        if course.get("moduleIndex")
                        else None
                    ),
                    "passing_percent": course.get("passingPercent"),
                },
                "avatar": avatar_used,
                "avatar_seconds": avatar_seconds,
                "broll": {
                    "provider": "+".join(sorted(broll_modes)) or "none",
                    "requested": broll_provider,
                    "clips": broll_used,
                    "layout": broll_layout if broll_used else None,
                },
                "presenter": presenter["layout"] if presenter else "none",
                "music": "bed" if music_src else "none",
                "assets_dir": str(assets_dir),
                "warnings": warnings,
                "render_seconds": render_seconds,
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

            return ToolResult(
                success=True,
                data=data,
                artifacts=[str(output_path)],
                cost_usd=0.0,
                duration_seconds=render_seconds,
                model="lesson-video-remotion",
            )
        finally:
            if cleanup:
                if props_path.exists():
                    props_path.unlink()
                if public_dir.exists():
                    shutil.rmtree(public_dir, ignore_errors=True)


def _tail(stdout: str, stderr: str, limit: int = 3000) -> str:
    text = (stdout or "") + ("\n" + stderr if stderr else "")
    return text.strip()[-limit:]
