# Edit Director — BrainBook Video Pipeline

## Output

`artifacts/edit_decisions.json` — the render contract the compose stage follows.

## Runtime Selection (MANDATORY)

The lesson deck is a branded, slide-based composition, so:

- **Remotion** — the only runtime that renders the `LessonVideo` composition
  (slide layout, presenter frame, progress, knowledge-check cards).
- **HyperFrames** (`hyperframes`) — rejected for this pipeline: there is no HTML
  counterpart to the lesson deck.
- **FFmpeg** — only for trimming or concatenating an already rendered lesson,
  never for authoring one.

Say this explicitly in `decision_log` rather than silently narrowing the choice.

## Decisions To Record

```json
{
  "renderer": "lesson_video",
  "runtime": "remotion",
  "composition": "LessonVideo",
  "voice_provider": "piper",
  "voice": "en_US-lessac-medium",
  "rate": 1.0,
  "captions": "auto",
  "caption_words_per_page": 4,
  "presenter": { "layout": "pip-br", "portrait_path": "…",
                 "name": "Avery Quinn", "role": "Training Instructor" },
  "avatar": { "provider": "auto", "engine": "wav2lip" },
  "broll": { "provider": "auto", "layout": "panel", "scope": "content" },
  "music": { "path": "…", "volume": 0.12 },
  "theme": "clean-professional",
  "course_source": "artifacts/bb_exam_source.json",
  "output": "assets/video/<module>.mp4"
}
```

## Rules

- **Presenter** is lip-synced when the talking-head service is running; the
  renderer falls back to the still portrait and says so. `avatar_provider: none`
  keeps it still on purpose.
- **B-roll** is a source decision: `files` (module paths), `stock` (Pexels key),
  `generated` (ComfyUI stills), or `auto`. Panel framing keeps text readable;
  `background` is for cinematic treatments only.
- **Course context** travels in `bb_exam_source.json`; never retype it into the
  module. If the file is absent, the module's course fields are the fallback.
- **Captions** are burned in by the renderer; `captions: "none"` is valid when
  the LMS player owns caption display.
- **Music** is a single bed at ≤ 0.15 volume, or omitted. It never replaces
  narration.
- **Theme** comes from the chosen playbook (`clean-professional`,
  `flat-motion-graphics`, …); the accent color may be overridden to match brand.
- **Render concurrency** is handled by the tool; don't hand-tune Remotion flags
  unless a render is timing out.

## Quality Gate

- every decision maps to a `lesson_video` input,
- the presenter/b-roll/caption choices match `asset_manifest.json`,
- the course source is named and exists (or the omission is deliberate),
- the output path and filename follow the publish naming rule,
- rejected runtimes carry a reason.
