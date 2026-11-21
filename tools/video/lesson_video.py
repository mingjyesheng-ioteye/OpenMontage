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

    capabilities = ["lesson_video_render", "narrated_slides", "word_captions"]
    supports = {
        "script_to_video": True,
        "captions": True,
        "music": True,
        "quiz": True,
        "presenter": True,
        "avatar": False,
    }
    best_for = [
        "BrainBook-branded lesson modules from a signed-off module JSON",
        "narrated training decks with word-level captions and a knowledge check",
        "local, offline renders through piper + Remotion",
    ]
    not_good_for = [
        "shot-by-shot cinematic video (use the cinematic pipeline)",
        "talking-head lip-sync video (use the avatar pipeline)",
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
            "presenter_layout": {
                "type": "string",
                "enum": ["none", "pip-br", "side-right", "full"],
                "description": "Presenter frame (default pip-br when a portrait is given)",
            },
            "presenter_name": {"type": "string"},
            "presenter_role": {"type": "string"},
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
        info["status_reason"] = (
            "" if self.get_status() == ToolStatus.AVAILABLE
            else "needs Node/npx + remotion-composer, ffmpeg/ffprobe, and a TTS provider"
        )
        return info

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
        # Stage media for Remotion (staticFile paths are public-relative)
        # --------------------------------------------------------------
        token = secrets.token_hex(4)
        public_dir = output_path.parent / f".lesson-public-{module_id}-{token}"
        public_dir.mkdir(parents=True, exist_ok=True)
        props_path = output_path.parent / f".lesson-props-{module_id}-{token}.json"
        cleanup = True

        try:
            for position, item in enumerate(timeline, start=1):
                source: Path | None = item.pop("audio_file")
                caption_words = item.pop("caption_words")
                item["captionWords"] = caption_words
                if source is None:
                    item["audioSrc"] = None
                    continue
                name = f"{module_id}-s{position:02d}{source.suffix}"
                shutil.copy2(source, public_dir / name)
                item["audioSrc"] = name

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
            portrait = inputs.get("portrait_path")
            layout = str(inputs.get("presenter_layout") or "")
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
            elif layout and layout != "none":
                warnings.append("presenter: layout requested without a portrait; skipped")

            assessment = module.get("assessment") or None
            questions = []
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

            fps = int(inputs.get("fps") or 30)
            width = int(inputs.get("width") or 1920)
            height = int(inputs.get("height") or 1080)
            props: dict[str, Any] = {
                "brand": module.get("brand"),
                "courseTitle": module.get("courseTitle"),
                "moduleTitle": module.get("moduleTitle"),
                "level": module.get("level"),
                "moduleIndex": module.get("moduleIndex"),
                "moduleTotal": module.get("moduleTotal"),
                "theme": inputs.get("theme") or "clean-professional",
                "accentColor": inputs.get("accent_color")
                or (module.get("presenter") or {}).get("accentColor"),
                "slides": timeline,
                "musicSrc": music_src,
                "musicVolume": float(inputs.get("music_volume") or 0.12),
                "presenter": presenter,
                "assessment": {"questions": questions} if questions else None,
                "showAssessment": inputs.get("show_assessment", True) is not False,
                "captions": captions_mode != "none",
                "wordsPerPage": int(inputs.get("caption_words_per_page") or 4),
                "quizSeconds": float(inputs.get("quiz_seconds") or 7),
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
                "course_title": module.get("courseTitle"),
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
