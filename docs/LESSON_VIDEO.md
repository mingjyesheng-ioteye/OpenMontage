# Lesson Video (native renderer)

`lesson_video` renders BrainBook-style training lessons inside OpenMontage. It
replaces the external `bb_video` renderer: narration is synthesized by an
OpenMontage TTS tool, the timeline is measured from the generated audio, and the
deck is rendered by the `LessonVideo` Remotion composition.

```
module JSON ──▶ TTS per slide ──▶ ffprobe timeline ──▶ captions ──▶ Remotion LessonVideo ──▶ MP4
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
| `output_path` | `<id>.mp4` | narration is cached in `<id>-lesson-assets/` next to it |
| `voice_provider` | `piper` | `azure`, `elevenlabs`, `openai` when keys are set |
| `voice` | provider default | piper model, Azure short name, ElevenLabs voice id, OpenAI voice |
| `rate` | `1.0` | speaking-rate multiplier |
| `captions` | `auto` | `none` disables burned-in captions |
| `caption_model` | `base` | faster-whisper model when transcription is used |
| `theme` | `clean-professional` | any playbook theme in `remotion-composer/src/Root.tsx` |
| `accent_color` | module / theme | `#RRGGBB` brand accent |
| `portrait_path` + `presenter_layout` | none | still portrait in `pip-br`, `side-right`, `full` |
| `music_path` + `music_volume` | none / 0.12 | one music bed under narration |
| `show_assessment` | `true` | trailing knowledge-check cards |
| `remotion_concurrency`, `timeout_seconds` | auto / 3600 | render tuning |

## Captions

Three modes, reported back in `data.captions`:

1. **provider** — ElevenLabs returns character alignment; the tool groups it into
   words. Exact timings.
2. **transcribed** — `transcriber` (faster-whisper) when installed.
3. **proportional** — words distributed across the measured audio. Readable, but
   timings drift inside a page; install faster-whisper for alignment.

## Cost and speed

Piper narration and Remotion frames are local and free. A 30 s module renders in
under a minute; a 6–8 minute module takes a few minutes, dominated by Remotion
frames. Narration is cached, so re-rendering after a layout change does not pay
for TTS again.

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
