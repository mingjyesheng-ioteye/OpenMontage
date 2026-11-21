# Compose Director — BrainBook Video Pipeline

## Output

`artifacts/render_report.json` plus the lesson MP4 in the project workspace.

**Runtime routing:** render with `render_runtime = "remotion"` through
`lesson_video`. That tool is native to OpenMontage: it synthesizes narration
with an OpenMontage voice provider, lip-syncs the presenter, sources b-roll and
renders the `LessonVideo` Remotion composition. HyperFrames (lowercase
`hyperframes`) is not a viable runtime here — there is no HTML counterpart to the
lesson deck — so record the decision with
`rejected_because: "the lesson deck exists only as the Remotion LessonVideo composition"`.
No external renderer checkout is required.

## Tool

Call **`lesson_video`** once with the approved module and providers:

```json
{
  "module_path": "projects/<id>/artifacts/lesson_module.json",
  "source_path": "projects/<id>/artifacts/bb_exam_source.json",
  "output_path": "projects/<id>/assets/video/<module>.mp4",
  "voice_provider": "piper",
  "voice": "en_US-lessac-medium",
  "rate": 1.0,
  "captions": "auto",
  "theme": "clean-professional",
  "portrait_path": "…/portrait.png",
  "presenter_layout": "pip-br",
  "presenter_name": "Avery Quinn",
  "presenter_role": "Training Instructor",
  "avatar_provider": "auto",
  "avatar_engine": "wav2lip",
  "broll_provider": "auto",
  "broll_layout": "panel",
  "broll_scope": "content",
  "music_path": "…/music.mp3",
  "music_volume": 0.12,
  "timeout_seconds": 3600
}
```

The tool performs:

1. **Course context** from `source_path` (auto-detected next to the module):
   course description, module position and passing threshold become title-slide
   chips. Without a source, the module's own course fields are used.
2. **Narration** per slide through the chosen OpenMontage TTS tool (`piper`
   local by default; `azure`, `elevenlabs`, `openai` when keys are configured).
   Narration is cached in `<module>-lesson-assets/narration/` and reused on a
   re-run — only delete it to force new audio.
3. **Lip sync**: narration + padding is concatenated into one track exactly as
   long as the video, then the talking-head service animates the portrait
   (`avatar_engine: wav2lip` is the fast default). The clip is muted and shown in
   the presenter frame; a still portrait is the fallback, reported in `warnings`.
4. **B-roll**: supplied `broll_path`, or stock footage when `PEXELS_API_KEY` is
   set, or a local ComfyUI still (slow push-in). Content slides get a right-hand
   panel by default. Clips are trimmed and muted to the slide.
5. **Captions** and the **Remotion render** of `LessonVideo`.

## Timing

Budget roughly: narration seconds × 0.3 (piper) + narration seconds × ~0.6
(wav2lip) + Remotion frames. A 45 s module renders in about a minute on this
machine; a 6–8 minute module takes several minutes. Stock download and still
generation add per-clip time. Do not kill a run that is still emitting progress.

## Failure Handling

- Tool `UNAVAILABLE` → the report names which dependency is missing. Check, in
  order: `npx` (Node), `remotion-composer/node_modules`, `ffmpeg`/`ffprobe`, and
  at least one TTS provider (`piper` locally; cloud voices need their keys).
  This is a blocker; do **not** substitute a still-image animatic.
- `Narration failed on slide N` → the voice or key is wrong, or the narration
  text is empty. Fix the provider plan or the module, then re-run.
- `avatar: …` warning → usually the talking-head service is down or the engine
  failed. The lesson still renders with a still portrait; say so in the report.
- `broll: …` warning → no stock key, ComfyUI down, or a supplied path is missing.
  Never invent footage; either provide `broll_path`, enable a source, or render
  without b-roll and record it.
- `Remotion render failed` → read the log tail. Usually a malformed slide
  (missing heading) or a staged asset that does not exist.

## Render Report

Record:

- output path, file size, duration, fps, resolution,
- `voice_provider` / `voice` actually used, `rate`, and `captions` mode,
- `avatar` engine (`wav2lip` / `cached` / `still`) and `avatar_seconds`,
- `broll` provider, clip count and layout,
- `course` title/description presence and module position,
- `narration_seconds`, slide count and per-slide durations,
- presenter layout, music (`bed` or `none`), `render_seconds`, module id,
- every `warnings` entry verbatim — it is the honest fallback record.
