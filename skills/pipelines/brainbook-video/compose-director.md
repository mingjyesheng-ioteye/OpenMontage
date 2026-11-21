# Compose Director — BrainBook Video Pipeline

## Output

`artifacts/render_report.json` plus the lesson MP4 in the project workspace.

**Runtime routing:** render with `render_runtime = "remotion"` through
`lesson_video`. That tool is native to OpenMontage: it synthesizes narration
with an OpenMontage voice provider and renders the `LessonVideo` Remotion
composition. HyperFrames (lowercase `hyperframes`) is not a viable runtime here —
there is no HTML counterpart to the lesson deck — so record the decision with
`rejected_because: "the lesson deck exists only as the Remotion LessonVideo composition"`.
No external renderer checkout is required.

## Tool

Call **`lesson_video`** once with the approved module and providers:

```json
{
  "module_path": "projects/<id>/artifacts/lesson_module.json",
  "output_path": "projects/<id>/assets/video/<module>.mp4",
  "voice_provider": "piper",
  "voice": "en_US-lessac-medium",
  "rate": 1.0,
  "captions": "auto",
  "theme": "clean-professional",
  "portrait_path": "…/portrait.png",
  "presenter_layout": "pip-br",
  "music_path": "…/music.mp3",
  "music_volume": 0.12,
  "timeout_seconds": 3600
}
```

The tool performs:

1. **Narration** per slide through the chosen OpenMontage TTS tool (`piper`
   local by default; `azure`, `elevenlabs`, `openai` when keys are configured).
   Narration is cached in `<module>-lesson-assets/narration/` and reused on a
   re-run — only delete it to force new audio.
2. **Timeline** from real `ffprobe` durations (plus ~0.55 s of breathing room
   per slide), so slide changes follow the voice instead of an estimate.
3. **Captions**: provider word timings (ElevenLabs), else `transcriber` when
   faster-whisper is installed, else proportional word timing. The report says
   which one ran (`captions: provider | transcribed | proportional | none`).
4. **Render** of the `LessonVideo` composition: deck layout, presenter frame,
   progress bar, and knowledge-check cards from `assessment.questions`.

## Timing

A 30 s module renders in under a minute on this machine; a 6–8 minute module
takes a few minutes, mostly Remotion frames. Do not kill a run that is still
emitting `Rendered n/N` lines.

## Failure Handling

- Tool `UNAVAILABLE` → the report names which dependency is missing. Check, in
  order: `npx` (Node), `remotion-composer/node_modules`, `ffmpeg`/`ffprobe`, and
  at least one TTS provider (`piper` locally; cloud voices need their keys).
  This is a blocker; do **not** substitute a still-image animatic.
- `Narration failed on slide N` → the voice or key is wrong, or the narration
  text is empty. Fix the provider plan or the module, then re-run.
- `Remotion render failed` → read the log tail. Usually a malformed slide
  (missing heading) or a staged asset that does not exist.
- Timeout → the render may still be running; check the output path before
  retrying.

## Render Report

Record:

- output path, file size, duration, fps, resolution,
- `voice_provider` / `voice` actually used, `rate`, and `captions` mode,
- `narration_seconds`, slide count and per-slide durations,
- presenter layout (`none` when no portrait) and music (`bed` or `none`),
- `render_seconds`, the module id, and any `warnings` returned by the tool,
- any `missing_capabilities` carried from the asset stage.
