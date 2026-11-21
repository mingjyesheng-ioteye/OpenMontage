# Asset Director — BrainBook Video Pipeline

## Output

`artifacts/asset_manifest.json` describing **what the renderer will generate**
and with which provider. The `lesson_video` tool owns narration synthesis,
lip-synced presenter, caption timing, b-roll and the render; this stage commits
to the plan and checks it is honest.

## Provider Matrix

| Role | Default | Other options | Notes |
|---|---|---|---|
| Voice | `piper` / `en_US-lessac-medium` | `azure` (`en-US-AndrewMultilingualNeural`), `elevenlabs`, `openai` | piper is local and free; ElevenLabs returns alignment, which gives exact caption timing |
| Presenter | lip sync via the local talking-head service (`wav2lip`) | still portrait (`avatar_provider: none`), `side-right`, `full` | wav2lip renders roughly in real time; musetalk/sadtalker are much slower |
| Captions | `auto` | `none` | auto = provider timings, else faster-whisper, else proportional word timing |
| Music | `none` | a local music bed | one bed under narration at volume ≤ 0.15 |
| B-roll | `auto` | `stock`, `generated`, `files`, `none` | auto: supplied `broll_path` → Pexels stock when `PEXELS_API_KEY` is set → local ComfyUI stills |

The tool reports what actually ran (`avatar`, `broll.provider`, `captions`).
Record that possibility in the manifest rather than discovering it in the render
report.

## Portrait

If a presenter is used, the manifest must point at a portrait that:

- exists on disk (`portrait_path`) and is at least 1024 px on the short edge,
- has one clearly framed face, neutral background, eyes toward camera,
- is rights-cleared for internal training use.

The same portrait drives both the lip-synced clip and the still fallback. Without
an approved portrait, choose `none` (slides only) rather than a generic stock
face.

## B-roll

Pick a mode and justify it:

- `files` — the module supplies `broll_path` per slide (best control, no network).
- `stock` — Pexels footage; needs `PEXELS_API_KEY`. Clip is trimmed/muted to the slide.
- `generated` — local ComfyUI stills (Qwen-Image / FLUX) with a slow push-in; needs
  the ComfyUI server running.
- `auto` — tries supplied file, then stock, then generated; skips quietly when none
  is possible and says so in `warnings`.

B-roll appears on content slides by default (`broll_scope: content`) in a right-hand
panel; `background` framing is available when the lesson is more cinematic.

## Manifest Shape

```json
{
  "module_id": "m01",
  "assets": [
    { "role": "narration", "provider": "piper", "voice": "en_US-lessac-medium",
      "per_slide": true, "notes": "duration measured with ffprobe at render" },
    { "role": "captions", "provider": "auto", "word_level": true },
    { "role": "presenter", "provider": "talking_head", "engine": "wav2lip",
      "portrait_path": "…", "layout": "pip-br", "lip_sync": true },
    { "role": "broll", "provider": "auto", "layout": "panel", "scope": "content" },
    { "role": "music", "provider": "none" }
  ],
  "missing_capabilities": []
}
```

## Quality Gate

- every requested asset names a provider and its inputs,
- unavailable providers are listed under `missing_capabilities` with the fallback
  that will actually run,
- the portrait reference exists and is rights-safe,
- b-roll mode is justified (stock key present / ComfyUI running / supplied files),
- music is justified — `none` is a valid, preferred default.
