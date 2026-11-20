# Compose Director — BrainBook Video Pipeline

## Output

`artifacts/render_report.json` plus the lesson MP4 in the project workspace.

**Runtime routing:** render with `render_runtime = "remotion"` through
`bb_video_lesson`. HyperFrames is not wired for this pipeline (the lesson exists
only as the `BrainBookLesson` Remotion composition). If the runtime or the
bb_video checkout is unavailable, escalate the blocker — never substitute a
different compositor.

## Tool

Call **`bb_video_lesson`** once with the approved module and providers:

```json
{
  "module_path": "projects/<id>/artifacts/lesson_module.json",
  "output_path": "projects/<id>/assets/video/<module>.mp4",
  "voice_provider": "kokoro",
  "voice": "af_heart",
  "avatar_provider": "kenburns",
  "portrait_path": "…",
  "presenter_layout": "pip-br",
  "captions": "auto",
  "music_provider": "none",
  "broll_provider": "none",
  "timeout_seconds": 5400
}
```

The tool runs `bb_video/scripts/build-lesson.sh`, which performs:

1. TTS narration → `public/bb_exam/<id>/sNN.mp3`
2. caption alignment (word timings)
3. presenter animation (kenburns / service / placeholder)
4. optional music and b-roll
5. Remotion render to the output path

It returns the probe (size, fps, duration), provider values actually used, and a
log tail. Renders usually take **2–6 minutes** for a module; do not kill a run
that is still emitting stage lines.

## Failure Handling

- Tool `UNAVAILABLE` → check `BB_VIDEO_DIR`, `node_modules/.bin/remotion`, and
  `ffmpeg`. This is a blocker; do **not** substitute a still-image animatic.
- `build failed` → read the log tail: missing voice key, unavailable avatar
  backend (falls back to `placeholder` with a printed reason), or a bad slide.
  Fix the module/provider plan and re-run.
- Timeout → the render may still be running; check the output path before
  retrying.

## Render Report

Record:

- output path, file size, duration, fps, resolution,
- providers actually used (including fallbacks),
- `render_seconds` and the module id,
- caption/music/b-roll decisions,
- any `missing_capabilities` carried from the asset stage.
