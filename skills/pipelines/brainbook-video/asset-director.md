# Asset Director — BrainBook Video Pipeline

## Output

`artifacts/asset_manifest.json` describing **what the renderer will generate**
and with which provider. The `lesson_video` tool owns narration synthesis,
caption timing and the render; this stage commits to the plan and checks it is
honest.

## Provider Matrix

| Role | Default | Other options | Notes |
|---|---|---|---|
| Voice | `piper` / `en_US-lessac-medium` | `azure` (`en-US-AndrewMultilingualNeural`), `elevenlabs`, `openai` | piper is local and free; cloud voices need their key in `.env`; ElevenLabs returns alignment, which gives exact caption timing |
| Captions | `auto` | `none` | auto = provider timings, else faster-whisper, else proportional word timing (reported honestly) |
| Presenter | `none` | still portrait in `pip-br`, `side-right`, `full` | a portrait frame with slow drift — **no lip sync** in this pipeline |
| Music | `none` | a local music bed | one bed under narration at volume ≤ 0.15 |
| B-roll | `none` | — | not supported by the lesson renderer |

The tool reports the caption mode it actually used
(`provider` / `transcribed` / `proportional` / `none`). Record that possibility
in the manifest rather than discovering it in the render report.

## Portrait

If a presenter is used, the manifest must point at a portrait that:

- exists on disk (`portrait_path`) and is at least 1024 px on the short edge,
- has one clearly framed face, neutral background, eyes toward camera,
- is rights-cleared for internal training use.

Without an approved portrait, choose `none` (slides only) rather than a generic
stock face.

## Manifest Shape

```json
{
  "module_id": "m01",
  "assets": [
    { "role": "narration", "provider": "piper", "voice": "en_US-lessac-medium",
      "per_slide": true, "notes": "duration measured with ffprobe at render" },
    { "role": "captions", "provider": "auto", "word_level": true },
    { "role": "presenter", "provider": "still-portrait", "portrait_path": "…",
      "layout": "pip-br", "lip_sync": false },
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
- music is justified — `none` is a valid, preferred default,
- any promised lip-sync is flagged as unsupported and redirected.
