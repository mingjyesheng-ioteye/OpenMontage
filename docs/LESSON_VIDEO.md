# Lesson Video (native renderer)

`lesson_video` renders BrainBook-style training lessons inside OpenMontage. It
replaces the external `bb_video` renderer: narration is synthesized by an
OpenMontage TTS tool, the timeline is measured from the generated audio, and the
deck is rendered by the `LessonVideo` Remotion composition.

```
module JSON ──▶ TTS per slide ──▶ ffprobe timeline ──▶ avatar track + lip sync
            └─▶ course context (bb_exam) ──▶ b-roll sourcing ──▶ Remotion LessonVideo ──▶ MP4
```

## Why it exists

The `brainbook-video` pipeline used to shell out to `~/bb_video/scripts/build-lesson.sh`,
which meant a lesson could only render on a machine with that checkout, its
node_modules and its Python venvs. The pipeline now owns the same stages, so the
renderer ships with OpenMontage and the only external requirements are Node,
`ffmpeg`, and a voice provider.

## Module contract

```json
{
  "id": "m01",
  "brand": "Arc Mercer — Center of Excellence",
  "courseTitle": "Understanding Behavior",
  "moduleTitle": "Functions and Causes of Behavior",
  "level": "Entry (E)",
  "moduleIndex": 1,
  "moduleTotal": 7,
  "slides": [
    { "kind": "title", "heading": "…", "sub": "…", "narration": "…" },
    { "kind": "objectives", "heading": "…", "bullets": ["…"], "narration": "…" },
    { "kind": "content", "heading": "…", "sub": "…", "bullets": ["…"], "narration": "…" },
    { "kind": "recap", "heading": "Recap", "bullets": ["…"], "narration": "…" }
  ],
  "assessment": {
    "passingPercent": 75,
    "questions": [ { "q": "…", "options": ["…"], "answer": 1 } ]
  }
}
```

- `narration` is spoken only; `bullets` / `sub` are what the slide shows.
- `kind` drives the layout: `title`, `objectives`, `content`, `recap`, `quiz`.
- Slides with no narration hold for ~4.5 s; the timeline is otherwise
  `audio duration + 0.55 s`.
- `assessment.questions` renders as trailing knowledge-check cards (7 s each,
  answer highlighted at 2.2 s) unless `show_assessment: false`.

## Tool inputs

| Input | Default | Notes |
|---|---|---|
| `module_path` | — | required |
| `source_path` | auto next to the module | `bb_exam_source.json`: course description, module position, passing threshold |
| `output_path` | `<id>.mp4` | narration is cached in `<id>-lesson-assets/` next to it |
| `voice_provider` | `piper` | `azure`, `elevenlabs`, `openai` when keys are set |
| `voice` | provider default | piper model, Azure short name, ElevenLabs voice id, OpenAI voice |
| `rate` | `1.0` | speaking-rate multiplier |
| `captions` | `auto` | `none` disables burned-in captions |
| `caption_model` | `base` | faster-whisper model when transcription is used |
| `theme` | `clean-professional` | any playbook theme in `remotion-composer/src/Root.tsx` |
| `accent_color` | module / theme | `#RRGGBB` brand accent |
| `portrait_path` + `presenter_layout` | none | lip-synced frame in `pip-br`, `side-right`, `full` |
| `avatar_provider` | `auto` | `none` keeps a still portrait; `auto` lip-syncs when the service runs |
| `avatar_engine` | `wav2lip` | `musetalk` / `sadtalker` are much slower; `wav2lip` is ~real time |
| `broll_provider` | `auto` | `files`, `stock`, `generated`, `none` |
| `broll_layout` | `panel` | `background` for a cinematic treatment |
| `broll_scope` | `content` | `all` adds b-roll to objectives/recap too |
| `music_path` + `music_volume` | none / 0.12 | one music bed under narration |
| `show_assessment` | `true` | trailing knowledge-check cards |
| `remotion_concurrency`, `timeout_seconds` | auto / 3600 | render tuning |

## Presenter lip sync

The tool concatenates per-slide narration plus the same padding the composition
uses, appends silence for the knowledge check, and sends that single track to the
local talking-head service (`TALKING_HEAD_SERVICE_URL`, default
`http://localhost:8691`). The service animates the portrait with `wav2lip`, which
runs at roughly real time on this workstation (a 6-minute lesson ≈ 4 minutes).
The clip is muted and placed in the presenter frame; per-slide narration remains
the audio source, so slide changes never break lip sync.

If the service is down or the engine fails, the render continues with the still
portrait and the report says so (`avatar: still` + a `warnings` entry). Narration
is cached, so retrying the avatar does not re-pay for TTS.

## B-roll

`auto` tries, in order:

1. a module-supplied `broll_path` (image or video, best control),
2. Pexels stock footage when `PEXELS_API_KEY` is set,
3. a local generated still (ComfyUI Qwen-Image/FLUX, or Unsloth Studio) with a slow push-in.

Content slides get a right-hand panel by default; `background` places the media
full-bleed behind a scrim. Video clips are trimmed and muted to the slide length,
images are staged as-is with a Ken Burns push. `broll.provider` in the report says
which source actually ran.

Without a key, a running ComfyUI, or supplied files the render continues without
b-roll and records one warning explaining how to enable it.

## Course information

The title slide shows the course title, level, module position, course
description, knowledge-check count and passing threshold. Those come from
`bb_exam_source.json` (written by BrainBook Studio when a course module is
chosen); the module's own `courseTitle` / `level` / `moduleIndex` /
`moduleTotal` are the fallback. Nothing is retyped by hand.

## Captions

Three modes, reported back in `data.captions`:

1. **provider** — ElevenLabs returns character alignment; the tool groups it into
   words. Exact timings.
2. **transcribed** — `transcriber` (faster-whisper) when installed.
3. **proportional** — words distributed across the measured audio. Readable, but
   timings drift inside a page; install faster-whisper for alignment.

## Cost and speed

Piper narration, wav2lip and Remotion frames are local and free. Budget roughly
narration × 0.3 for TTS, narration × 0.6 for lip sync, plus Remotion time. A 45 s
module renders in about a minute; a 6–8 minute module takes several minutes.
Narration and the avatar clip are cached in `<id>-lesson-assets/`, so re-rendering
after a layout change does not pay for either again.

## Delivery

`bb_exam_media` uploads the finished MP4 (or captions/thumbnail) into the course
version the lesson was authored for. It resolves the published course from its
slug and reads credentials from `BB_EXAM_TOKEN` or
`BB_EXAM_EMAIL`/`BB_EXAM_PASSWORD`. Publishing is an explicit publish-stage
action, never an automatic one.

## Verification

```bash
.venv/bin/python -m pytest tests/tools/test_lesson_video.py tests/tools/test_bb_exam_media.py -q
```

A real smoke render only needs a short module and local piper:

```bash
.venv/bin/python - <<'PY'
from tools.video.lesson_video import LessonVideo
res = LessonVideo().execute({"module_path": "lesson.json", "output_path": "out.mp4"})
print(res.success, res.data.get("duration_seconds"), res.data.get("captions"))
PY
```
