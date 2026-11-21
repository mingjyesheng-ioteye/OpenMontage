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
  "presenter": { "layout": "pip-br", "portrait_path": "…" },
  "music": { "path": "…", "volume": 0.12 },
  "theme": "clean-professional",
  "output": "assets/video/<module>.mp4"
}
```

## Rules

- **Presenter** is a still portrait in a frame (`pip-br`, `side-right`), not a
  lip-synced avatar. If the brief promised lip sync, record
  `missing_capabilities: ["lip_sync"]` and offer the avatar pipeline instead.
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
- the presenter/music/caption choices match `asset_manifest.json`,
- the output path and filename follow the publish naming rule,
- rejected runtimes carry a reason.
