# Compose Director — BrainBook Studio Pipeline

## When To Use

After the edit decisions are written. Render the deliverable with the locked
runtime and verify the file, not the intent.

## Inputs

- `artifacts/edit_decisions.json` (`render_runtime`, `composition_mode`, cuts,
  audio, subtitles)
- `artifacts/asset_manifest.json`
- `output/` or `renders/` destination from the brief

## Process

1. **Route by the locked runtime.** Pass
   `edit_decisions.render_runtime` to `video_compose` and do not override it.
   - `remotion` — stills with spring motion, captions, charts, audio mix.
   - `hyperframes` — HTML/CSS/GSAP motion, type-led pieces, overlays.
   - `ffmpeg` — straight cuts/concat only.
2. **Honor the authoring mode.** `templated` uses the stock scene types;
   `atelier` means the composition was authored for this piece. Do not fall back
   from atelier to a stock template without approval.
3. **Render, then verify.**
   - `ffprobe`: resolution, fps, duration, audio stream (when expected).
   - watch/sample at least three frames across the timeline for black frames,
     frozen video, or caption drift.
   - confirm loudness is sane and narration is audible over music.
4. **Failures are blockers.** If the runtime is missing, a render errors, or the
   output is silent/black, stop and report: what was attempted, what failed,
   whether it is environment or creative, options, and your recommendation. Do
   not substitute FFmpeg for a motion runtime without approval.

## Artifact

Write `artifacts/render_report.json` per
`schemas/artifacts/render_report.schema.json`.

Required: `version`, `outputs`. Also record `render_time_seconds`,
`render_grammar`, `warnings`, and `verification_notes` describing the checks you
actually ran.

## Quality Gate

- the final file exists and probes cleanly,
- duration and aspect match the brief,
- audio is present when the plan promised it,
- captions are burned or sidecar as decided,
- warnings are empty or explained.

No human gate on this stage.
