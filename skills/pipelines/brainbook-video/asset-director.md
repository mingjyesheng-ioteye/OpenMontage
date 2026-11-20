# Asset Director — BrainBook Video Pipeline

## Output

`artifacts/asset_manifest.json` describing **what the renderer will generate**
and with which provider. `bb_video` owns the actual TTS, caption alignment,
presenter animation, music and b-roll work during compose; this stage commits to
the plan and checks it is honest.

## Provider Matrix

| Role | Default | Other options | Notes |
|---|---|---|---|
| Voice | `kokoro` / `af_heart` | `edge` (`en-US-AvaNeural`), `elevenlabs`, `openai`, `azure`, `external` | kokoro is local; edge needs network; cloud providers need keys in `bb_video/.env` |
| Captions | `auto` | `none`, `fast` | `auto` aligns word timings when the voice has none (kokoro) |
| Presenter | `kenburns` | `placeholder`, `none`, `sadtalker_gpu`, `heygen`, `d_id`, `fal`, `replicate`, `kling` | `sadtalker_gpu` uses the Windows GPU avatar service; `kenburns` animates a still portrait without lip sync |
| Music | `none` | `acestep` (local), `elevenlabs`, `fal`, `replicate`, `openrouter` | keep music under narration or omit it |
| B-roll | `none` | `wan` (local), `replicate`, `fal`, `openrouter` | modes `t2v` / `i2v`; scope `module` (one clip) or `all` (per slide) |

`bb_video` downgrades an unavailable GPU/cloud presenter to `placeholder` and
prints the reason. Record that possibility in the manifest rather than
discovering it in the render log.

## Portrait

If a presenter is used, the manifest must point at a portrait that:

- exists on disk (`portrait_path`) and is at least 1024 px on the short edge,
- has one clearly framed face, neutral background, eyes toward camera,
- is rights-cleared for internal training use.

Without a portrait, choose `kenburns` only if a stock presenter image is
approved; otherwise use `placeholder` (animated card, no face).

## Manifest Shape

```json
{
  "module_id": "m01",
  "assets": [
    { "role": "narration", "provider": "kokoro", "voice": "af_heart",
      "per_slide": true, "notes": "duration comes from TTS at render" },
    { "role": "captions", "provider": "auto", "word_level": true },
    { "role": "presenter", "provider": "kenburns", "portrait_path": "…",
      "layout": "pip-br", "lip_sync": false },
    { "role": "music", "provider": "none" },
    { "role": "broll", "provider": "none" }
  ],
  "missing_capabilities": []
}
```

## Quality Gate

- every requested asset names a provider and its inputs,
- unavailable providers are listed under `missing_capabilities` with the fallback
  that will actually run,
- the portrait/b-roll references exist and are rights-safe,
- music and b-roll are justified — `none` is a valid, preferred default.
