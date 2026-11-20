# Edit Director — BrainBook Video Pipeline

## Output

`artifacts/edit_decisions.json` — the render settings handed to compose. In this
pipeline the *module JSON is the timeline*: there is no manual cut list, and no
Remotion source edits.

**Runtime routing:** `render_runtime` must be `remotion` for this pipeline.
HyperFrames is not an option here — `bb_video` ships a single Remotion
composition (`BrainBookLesson`) and there is no HyperFrames counterpart. If the
locked runtime is somehow unavailable, stop and surface the constraint; do not
silently swap runtimes.

## Decisions To Record

```json
{
  "render_runtime": "remotion",
  "renderer": "bb_video_lesson",
  "module_path": "artifacts/lesson_module.json",
  "voice_provider": "kokoro",
  "voice": "af_heart",
  "caption_mode": "auto",
  "presenter": { "provider": "kenburns", "layout": "pip-br",
                 "portrait_path": "…" },
  "music": { "provider": "none" },
  "broll": { "provider": "none", "mode": "t2v", "scope": "module" },
  "render": { "output_path": "assets/video/m01.mp4" }
}
```

Copy provider values from the asset manifest — do not invent new ones here. If a
decision changes after review (e.g. presenter switched to `placeholder`), append
a `decision_log` entry with the same category and subject.

## Rules

- **Presenter layout** is one of `pip-br`, `side-right`, `side-left`, `full`.
  `pip-br` keeps the slide readable; `full` is only for a talking-head opener.
- **Captions stay on** unless the brief explicitly waives them; word-level
  captions are a core deliverable of this pipeline.
- **Music** never sits above the narration; if a track is used, keep it under
  -18 dB relative to voice or skip it.
- **Do not trim slides manually.** If timing is wrong, fix narration length in
  the script stage and re-run; the renderer derives durations from audio.
- **Render concurrency** is handled by `bb_video` (CPU-bound); don't add flags.

## Quality Gate

- every field compose needs is present and matches the asset manifest,
- the output path lives under the project workspace,
- changed decisions have decision-log entries.
